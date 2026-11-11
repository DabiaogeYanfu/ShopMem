"""LoRA SFT of ShopMem-Writer (Qwen3-4B) on the chat samples of memeval.sft_data (v2 phase 1, step 7).

Both Writer roles (extractor, manager) are one model; a sample is its system + user prompt and the
teacher's reply. The prompt is rendered by the Qwen3 chat template with thinking off -- as the
Writer is served (enable_thinking=false) -- and only the reply tokens plus <|im_end|> carry loss.
The loss is token-level over each optimizer step (every reply token weighs the same, Memory-R2's
finding), and the LM head runs on reply positions only, so the 150k-vocabulary logits of the long
prompts never sit in GPU memory. Val loss is logged at every epoch end and each non-final epoch's
adapter is kept (adapter_ep<n>). After training, the adapter is saved and a few val prompts are
decoded greedily to check that the replies are well-formed JSON Lines closed by <END>.

Runs in envs/sft (torch, transformers, peft); it does not import memeval.

    CUDA_VISIBLE_DEVICES=0 ~/envs/sft/bin/python scripts/train_writer_sft.py \
        --data runs/teacher_train_K20/sft --out runs/sft_writer_smoke --epochs 1
"""
import argparse
import json
import math
import random
import time
from pathlib import Path

import torch
import torch.nn.functional as F
from peft import LoraConfig, get_peft_model
from transformers import AutoModelForCausalLM, AutoTokenizer, get_cosine_schedule_with_warmup

GEN_PROMPT_END = '<think>\n\n</think>\n\n'
END_MARK = '<END>'


def read_jsonl(path):
    return [json.loads(line) for line in open(path) if line.strip()]


def encode(tok, row, max_len):
    """(input ids, prompt length) or None if the sample is longer than max_len."""
    msgs = row['messages']
    prompt = tok.apply_chat_template(msgs[:-1], tokenize=False, add_generation_prompt=True, enable_thinking=False)
    assert prompt.endswith(GEN_PROMPT_END), 'chat template does not render the no-thinking generation prompt'
    p = tok(prompt, add_special_tokens=False)['input_ids']
    r = tok(msgs[-1]['content'] + '<|im_end|>', add_special_tokens=False)['input_ids']
    return (p + r, len(p)) if len(p) + len(r) <= max_len else None


def reply_loss(model, ids, n_prompt):
    """Summed cross-entropy of the reply tokens, and their count."""
    x = torch.tensor([ids], device=model.device)
    inner = model.get_base_model()
    h = inner.model(input_ids=x).last_hidden_state[0, n_prompt - 1:-1]
    logits = inner.lm_head(h).float()
    return F.cross_entropy(logits, x[0, n_prompt:], reduction='sum'), len(ids) - n_prompt


@torch.no_grad()
def evaluate(model, data):
    model.eval()
    tot = {}
    for ids, n_prompt, role in data:
        loss, n = reply_loss(model, ids, n_prompt)
        s = tot.setdefault(role, [0.0, 0])
        s[0] += loss.item()
        s[1] += n
    model.train()
    return {role: s[0] / s[1] for role, s in tot.items()}


@torch.no_grad()
def generate_check(model, tok, rows, max_new_tokens):
    """Greedy replies on val prompts: well-formed JSON Lines with <END>?"""
    model.eval()
    model.config.use_cache = True
    out = []
    for row in rows:
        prompt = tok.apply_chat_template(row['messages'][:-1], tokenize=False, add_generation_prompt=True,
                                         enable_thinking=False)
        x = tok(prompt, return_tensors='pt', add_special_tokens=False).to(model.device)
        y = model.generate(**x, max_new_tokens=max_new_tokens, do_sample=False)
        text = tok.decode(y[0, x['input_ids'].shape[1]:], skip_special_tokens=True)
        body = text.split(END_MARK, 1)[0]
        lines = [s.strip() for s in body.splitlines() if s.strip()]
        bad = 0
        for s in lines:
            try:
                json.loads(s)
            except json.JSONDecodeError:
                bad += 1
        out.append({'writer_role': row['writer_role'], 'history_id': row['history_id'], 'window': row['window'],
                    'complete': END_MARK in text, 'lines': len(lines), 'bad_lines': bad,
                    'teacher_lines': len([s for s in row['messages'][-1]['content'].split(END_MARK)[0].splitlines()
                                          if s.strip()]),
                    'reply': text, 'teacher': row['messages'][-1]['content']})
    return out


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--data', required=True, help='dir with train.jsonl / val.jsonl from memeval.sft_data')
    p.add_argument('--out', required=True)
    p.add_argument('--model', default='models/Qwen3-4B')
    p.add_argument('--max-len', type=int, default=6144)
    p.add_argument('--epochs', type=float, default=2)
    p.add_argument('--lr', type=float, default=1e-4)
    p.add_argument('--tokens-per-step', type=int, default=0,
                   help='accumulate samples until this many reply tokens (0: --accum samples per step)')
    p.add_argument('--accum', type=int, default=8)
    p.add_argument('--warmup', type=float, default=0.05)
    p.add_argument('--lora-r', type=int, default=16)
    p.add_argument('--lora-alpha', type=int, default=32)
    p.add_argument('--lora-dropout', type=float, default=0.05)
    p.add_argument('--eval-every', type=int, default=0, help='optimizer steps between val evaluations (0: per epoch)')
    p.add_argument('--gen-check', type=int, default=4, help='val prompts decoded after training')
    p.add_argument('--mem-fraction', type=float, default=0.0, help='cap on this process\'s share of GPU memory')
    p.add_argument('--seed', type=int, default=0)
    a = p.parse_args()

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / 'args.json').write_text(json.dumps(vars(a), indent=2))
    random.seed(a.seed)
    torch.manual_seed(a.seed)
    if a.mem_fraction:
        torch.cuda.set_per_process_memory_fraction(a.mem_fraction)

    tok = AutoTokenizer.from_pretrained(a.model)
    rows = {s: read_jsonl(Path(a.data) / f'{s}.jsonl') for s in ('train', 'val')}
    data, dropped = {}, {}
    for s, rs in rows.items():
        enc = [(encode(tok, r, a.max_len), r['writer_role']) for r in rs]
        data[s] = [(e[0], e[1], role) for e, role in enc if e]
        dropped[s] = sum(e is None for e, _ in enc)
    stats = {s: {'samples': len(d), 'dropped_too_long': dropped[s],
                 'reply_tokens': sum(len(i) - n for i, n, _ in d), 'max_len': max((len(i) for i, _, _ in d), default=0)}
             for s, d in data.items()}
    print(json.dumps(stats))

    model = AutoModelForCausalLM.from_pretrained(a.model, dtype=torch.bfloat16, device_map={'': 0},
                                                 attn_implementation='sdpa')
    model.config.use_cache = False
    model.gradient_checkpointing_enable(gradient_checkpointing_kwargs={'use_reentrant': False})
    model.enable_input_require_grads()
    model = get_peft_model(model, LoraConfig(r=a.lora_r, lora_alpha=a.lora_alpha, lora_dropout=a.lora_dropout,
                                             task_type='CAUSAL_LM',
                                             target_modules=['q_proj', 'k_proj', 'v_proj', 'o_proj',
                                                             'gate_proj', 'up_proj', 'down_proj']))
    model.print_trainable_parameters()

    def batches(epoch_data):
        group, ntok = [], 0
        for item in epoch_data:
            group.append(item)
            ntok += len(item[0]) - item[1]
            if (a.tokens_per_step and ntok >= a.tokens_per_step) or (not a.tokens_per_step and len(group) == a.accum):
                yield group
                group, ntok = [], 0
        if group:
            yield group

    n_epochs = math.ceil(a.epochs)
    epoch_sizes = [len(data['train']) if e + 1 <= a.epochs else int(len(data['train']) * (a.epochs - e))
                   for e in range(n_epochs)]
    orders = []
    for size in epoch_sizes:
        d = list(data['train'])
        random.shuffle(d)
        orders.append(d[:size])
    total_steps = sum(len(list(batches(o))) for o in orders)
    opt = torch.optim.AdamW([x for x in model.parameters() if x.requires_grad], lr=a.lr, weight_decay=0.0)
    sched = get_cosine_schedule_with_warmup(opt, int(a.warmup * total_steps), total_steps)

    log = open(out / 'log.jsonl', 'w')

    def record(entry):
        entry |= {'time': round(time.time() - t0, 1), 'peak_mem_gb': round(torch.cuda.max_memory_allocated() / 2**30, 2)}
        print(json.dumps(entry), flush=True)
        log.write(json.dumps(entry) + '\n')
        log.flush()

    t0, step = time.time(), 0
    record({'step': 0, 'val': evaluate(model, data['val'])})
    model.train()
    for epoch, order in enumerate(orders):
        for group in batches(order):
            ntok = sum(len(i) - n for i, n, _ in group)
            total = 0.0
            for ids, n_prompt, _ in group:
                loss, _ = reply_loss(model, ids, n_prompt)
                (loss / ntok).backward()
                total += loss.item()
            torch.nn.utils.clip_grad_norm_([x for x in model.parameters() if x.requires_grad], 1.0)
            opt.step()
            sched.step()
            opt.zero_grad(set_to_none=True)
            step += 1
            entry = {'step': step, 'epoch': epoch, 'loss': total / ntok, 'reply_tokens': ntok,
                     'lr': sched.get_last_lr()[0]}
            if a.eval_every and step % a.eval_every == 0:
                entry['val'] = evaluate(model, data['val'])
            record(entry)
        end = {'step': step, 'epoch': epoch}
        if not (a.eval_every and step % a.eval_every == 0):  # val loss at every epoch end, to pick the epoch
            end['val'] = evaluate(model, data['val'])
        if epoch + 1 < len(orders):  # keep each epoch's adapter, so a later epoch that overfits needs no rerun
            model.save_pretrained(out / f'adapter_ep{epoch + 1}')
            end['saved'] = f'adapter_ep{epoch + 1}'
        if len(end) > 2:
            record(end)

    model.save_pretrained(out / 'adapter')
    tok.save_pretrained(out / 'adapter')
    if a.gen_check:
        model.gradient_checkpointing_disable()
        val = rows['val']
        picks = [r for r in val if r['writer_role'] == 'extractor'][:a.gen_check // 2 or 1] + \
                [r for r in val if r['writer_role'] == 'manager'][:a.gen_check // 2]
        gens = generate_check(model, tok, picks, max_new_tokens=1024)
        (out / 'gen_check.jsonl').write_text(''.join(json.dumps(g, ensure_ascii=False) + '\n' for g in gens))
        record({'gen_check': [{k: g[k] for k in ('writer_role', 'complete', 'lines', 'bad_lines', 'teacher_lines')}
                              for g in gens]})
    (out / 'data_stats.json').write_text(json.dumps(stats, indent=2))


if __name__ == '__main__':
    main()
