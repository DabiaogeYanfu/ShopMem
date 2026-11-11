"""LoRA SFT of ShopMem-Reader (Qwen3-8B) on the cold-start trajectories of memeval.reader_cold
(方法论_v3_大纲 §3.4.3 step 1; 实验计划_v3 stage R1).

A trajectory (system, first retrieval + question, reply, search results, reply, ...) becomes one
sample per Reader reply: the prompt is the conversation up to that reply, rendered by the Qwen3 chat
template with the no-thinking generation prompt -- exactly what vLLM renders when the Reader is
served at that step (in one concatenated sequence earlier replies would render without the empty
think block the model generated after) -- and only the reply tokens plus <|im_end|> carry loss.
Search results and the question never carry loss. Normalization: every sample (one Reader reply)
weighs the same -- its summed loss is divided by its own reply tokens, then averaged over the step's
samples (方法论_v3 §3.4.3). Search replies are short and answer replies long; a token-level mean over
the step (train_writer_sft.py) would leave the search replies, the behaviour the cold start is for,
little weight. Val loss per epoch, the same per-sample mean, by reply kind (search / answer); each
non-final epoch's adapter is kept. After training a few val prompts are decoded greedily and
checked with the same strict action check as the episodes (memeval.reader_format: standard library
only, so it imports in the sft env).

    CUDA_VISIBLE_DEVICES=0 ~/envs/sft/bin/python scripts/train_reader_sft.py \
        --data runs/reader_cold_K20/sft --out runs/reader_sft_K20 --epochs 2
"""
import argparse
import json
import math
import random
import sys
import time
from pathlib import Path

import torch
from peft import LoraConfig, get_peft_model
from transformers import AutoModelForCausalLM, AutoTokenizer, get_cosine_schedule_with_warmup

from train_writer_sft import encode, read_jsonl, reply_loss

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from memeval.reader_format import parse_action  # noqa: E402


def action_of(text):
    act = parse_action(text)
    return act['action'] if act else None


@torch.no_grad()
def evaluate(model, data):
    """Val loss per reply kind: mean over samples of (summed reply loss / reply tokens)."""
    model.eval()
    tot = {}
    for ids, n_prompt, kind in data:
        loss, n = reply_loss(model, ids, n_prompt)
        s = tot.setdefault(kind, [0.0, 0])
        s[0] += loss.item() / n
        s[1] += 1
    model.train()
    return {kind: s[0] / s[1] for kind, s in tot.items()}


def turn_samples(row):
    """One sample per assistant reply: {'messages': conversation up to and including it, 'writer_role':
    the reply's kind (search / answer / other; the key name is what encode() and evaluate() read)}."""
    msgs, out = row['messages'], []
    for i, m in enumerate(msgs):
        if m['role'] == 'assistant':
            out.append({'messages': msgs[:i + 1], 'writer_role': action_of(m['content']) or 'other',
                        'question_id': row['question_id']})
    return out


@torch.no_grad()
def generate_check(model, tok, samples, max_new_tokens):
    model.eval()
    model.config.use_cache = True
    out = []
    for s in samples:
        prompt = tok.apply_chat_template(s['messages'][:-1], tokenize=False, add_generation_prompt=True,
                                         enable_thinking=False)
        x = tok(prompt, return_tensors='pt', add_special_tokens=False).to(model.device)
        y = model.generate(**x, max_new_tokens=max_new_tokens, do_sample=False)
        text = tok.decode(y[0, x['input_ids'].shape[1]:], skip_special_tokens=True)
        out.append({'question_id': s['question_id'], 'teacher_kind': s['writer_role'], 'kind': action_of(text),
                    'reply': text, 'teacher': s['messages'][-1]['content']})
    return out


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--data', required=True, help='dir with train.jsonl / val.jsonl from memeval.reader_cold build')
    p.add_argument('--out', required=True)
    p.add_argument('--model', default='models/Qwen3-8B')
    p.add_argument('--max-len', type=int, default=8192)
    p.add_argument('--epochs', type=float, default=2)
    p.add_argument('--lr', type=float, default=5e-5)
    p.add_argument('--accum', type=int, default=16, help='samples (Reader replies) per optimizer step')
    p.add_argument('--warmup', type=float, default=0.05)
    p.add_argument('--lora-r', type=int, default=16)
    p.add_argument('--lora-alpha', type=int, default=32)
    p.add_argument('--lora-dropout', type=float, default=0.05)
    p.add_argument('--gen-check', type=int, default=6, help='val replies decoded after training')
    p.add_argument('--seed', type=int, default=0)
    a = p.parse_args()

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / 'args.json').write_text(json.dumps(vars(a), indent=2))
    random.seed(a.seed)
    torch.manual_seed(a.seed)

    tok = AutoTokenizer.from_pretrained(a.model)
    samples = {s: [t for r in read_jsonl(Path(a.data) / f'{s}.jsonl') for t in turn_samples(r)] for s in ('train', 'val')}
    data, dropped = {}, {}
    for s, rs in samples.items():
        enc = [(encode(tok, r, a.max_len), r['writer_role']) for r in rs]
        data[s] = [(e[0], e[1], kind) for e, kind in enc if e]
        dropped[s] = sum(e is None for e, _ in enc)
    stats = {s: {'samples': len(d), 'dropped_too_long': dropped[s],
                 'by_kind': {k: sum(x[2] == k for x in d) for k in ('search', 'answer', 'other')},
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

    n_epochs = math.ceil(a.epochs)
    orders = []
    for e in range(n_epochs):
        d = list(data['train'])
        random.shuffle(d)
        orders.append(d if e + 1 <= a.epochs else d[:int(len(d) * (a.epochs - e))])
    groups = [[o[i:i + a.accum] for i in range(0, len(o), a.accum)] for o in orders]
    total_steps = sum(len(g) for g in groups)
    params = [x for x in model.parameters() if x.requires_grad]
    opt = torch.optim.AdamW(params, lr=a.lr, weight_decay=0.0)
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
    for epoch, epoch_groups in enumerate(groups):
        for group in epoch_groups:
            ntok = sum(len(i) - n for i, n, _ in group)
            total = 0.0
            for ids, n_prompt, _ in group:
                loss, n = reply_loss(model, ids, n_prompt)
                (loss / n / len(group)).backward()
                total += loss.item() / n / len(group)
            torch.nn.utils.clip_grad_norm_(params, 1.0)
            opt.step()
            sched.step()
            opt.zero_grad(set_to_none=True)
            step += 1
            record({'step': step, 'epoch': epoch, 'loss': total, 'reply_tokens': ntok, 'lr': sched.get_last_lr()[0]})
        end = {'step': step, 'epoch': epoch, 'val': evaluate(model, data['val'])}
        if epoch + 1 < len(groups):
            model.save_pretrained(out / f'adapter_ep{epoch + 1}')
            end['saved'] = f'adapter_ep{epoch + 1}'
        record(end)

    model.save_pretrained(out / 'adapter')
    tok.save_pretrained(out / 'adapter')
    if a.gen_check:
        model.gradient_checkpointing_disable()
        val = samples['val']
        picks = [s for s in val if s['writer_role'] == 'search'][:a.gen_check // 2] + \
                [s for s in val if s['writer_role'] == 'answer'][:a.gen_check - a.gen_check // 2]
        gens = generate_check(model, tok, picks, max_new_tokens=512)
        (out / 'gen_check.jsonl').write_text(''.join(json.dumps(g, ensure_ascii=False) + '\n' for g in gens))
        record({'gen_check': [{k: g[k] for k in ('teacher_kind', 'kind')} for g in gens]})
    (out / 'data_stats.json').write_text(json.dumps(stats, indent=2))


if __name__ == '__main__':
    main()
