"""Offline TOPR update of ShopMem-Writer (Qwen3-4B LoRA) on one sampling round (v2 phase 2; 方法论_v2 §3.3.3).

Data: memeval.topr_data chat samples, each with the advantage â of its sampled write. Starting from
the Writer that sampled the round (--init-adapter, the behaviour policy μ), per sample with reply
log-probability log π(τ) over its n reply tokens (Qwen3 template, thinking off, as in SFT):
  â ≥ 0 (positives, Writer or teacher):  loss = −â · log π(τ) / n          (SFT-like, no weight)
  â < 0 (negatives, Writer only):        loss = −[π(τ)/μ(τ)]_0^1 · â · log π(τ) / n
so the importance ratio, detached, only ever shrinks a negative's push (TOPR, Le Roux et al. 2025).
log μ(τ) is computed once before the first update with the initial adapter (the sampling policy:
Writer samples were drawn at temperature 1). Loss is length-normalised per sample (one Writer step)
and averaged over the samples of an optimiser step; no KL term, no dropout (so π/μ starts at 1). Val logs the mean per-token log π of
positives and negatives separately (positives should rise, negatives fall). Adapter per epoch, plus
adapter_step<k> every --save-every steps; log μ is cached in <out>/behaviour_logprobs.json (keyed by the
train.jsonl md5 and the init adapter) so a rerun on the same data skips the recompute.

Runs in the sft env (torch, transformers, peft), like train_writer_sft.py whose helpers it reuses:

    python scripts/train_writer_topr.py --data runs/topr1_train_K20/topr \
        --init-adapter runs/sft_writer_K20/adapter_ep3 --out runs/topr1_writer --epochs 1
"""
import argparse
import hashlib
import json
import math
import random
import time
from pathlib import Path

import torch
from peft import LoraConfig, PeftModel, get_peft_model
from transformers import AutoModelForCausalLM, AutoTokenizer, get_cosine_schedule_with_warmup

from train_writer_sft import encode, generate_check, read_jsonl, reply_loss


def log_prob(model, ids, n_prompt):
    """Summed log-probability of the reply tokens, and their count."""
    loss, n = reply_loss(model, ids, n_prompt)
    return -loss, n


@torch.no_grad()
def behaviour_logprobs(model, data):
    model.eval()
    out = [log_prob(model, ids, n_prompt)[0].item() if adv < 0 else None for ids, n_prompt, adv, _ in data]
    model.train()
    return out


@torch.no_grad()
def evaluate(model, data):
    """Mean per-token log π of the positive and of the negative val samples."""
    model.eval()
    tot = {'pos': [0.0, 0], 'neg': [0.0, 0]}
    for ids, n_prompt, adv, _ in data:
        lp, n = log_prob(model, ids, n_prompt)
        s = tot['pos' if adv >= 0 else 'neg']
        s[0] += lp.item()
        s[1] += n
    model.train()
    return {k: s[0] / s[1] for k, s in tot.items() if s[1]}


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--data', required=True, help='dir with train.jsonl / val.jsonl from memeval.topr_data')
    p.add_argument('--init-adapter', help='the sampling Writer\'s LoRA adapter (none: a fresh LoRA, for smoke tests)')
    p.add_argument('--out', required=True)
    p.add_argument('--model', default='models/Qwen3-4B')
    p.add_argument('--max-len', type=int, default=6144)
    p.add_argument('--epochs', type=float, default=1)
    p.add_argument('--lr', type=float, default=2e-5)
    p.add_argument('--accum', type=int, default=16, help='samples per optimiser step')
    p.add_argument('--warmup', type=float, default=0.05)
    p.add_argument('--eval-every', type=int, default=10)
    p.add_argument('--save-every', type=int, default=0, help='also keep adapter_step<k> every k steps (0: off)')
    p.add_argument('--gen-check', type=int, default=4)
    p.add_argument('--seed', type=int, default=0)
    a = p.parse_args()

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / 'args.json').write_text(json.dumps(vars(a), indent=2))
    random.seed(a.seed)
    torch.manual_seed(a.seed)

    tok = AutoTokenizer.from_pretrained(a.model)
    rows = {s: read_jsonl(Path(a.data) / f'{s}.jsonl') for s in ('train', 'val')}
    data, dropped = {}, {}
    for s, rs in rows.items():
        enc = [(encode(tok, r, a.max_len), r) for r in rs]
        data[s] = [(e[0], e[1], r['advantage'], r['writer_role']) for e, r in enc if e]
        dropped[s] = sum(e is None for e, _ in enc)
    stats = {s: {'samples': len(d), 'dropped_too_long': dropped[s], 'positives': sum(x[2] >= 0 for x in d),
                 'negatives': sum(x[2] < 0 for x in d)} for s, d in data.items()}
    print(json.dumps(stats))

    model = AutoModelForCausalLM.from_pretrained(a.model, dtype=torch.bfloat16, device_map={'': 0},
                                                 attn_implementation='sdpa')
    model.config.use_cache = False
    model.gradient_checkpointing_enable(gradient_checkpointing_kwargs={'use_reentrant': False})
    model.enable_input_require_grads()
    if a.init_adapter:
        model = PeftModel.from_pretrained(model, a.init_adapter, is_trainable=True)
    else:
        model = get_peft_model(model, LoraConfig(r=16, lora_alpha=32, lora_dropout=0.05, task_type='CAUSAL_LM',
                                                 target_modules=['q_proj', 'k_proj', 'v_proj', 'o_proj',
                                                                 'gate_proj', 'up_proj', 'down_proj']))
    for m in model.modules():  # no dropout: the ratio π/μ must compare the policies, not dropout noise
        if isinstance(m, torch.nn.Dropout):
            m.p = 0.0
    model.print_trainable_parameters()

    t0 = time.time()
    key = {'train_md5': hashlib.md5((Path(a.data) / 'train.jsonl').read_bytes()).hexdigest(),
           'init_adapter': a.init_adapter, 'max_len': a.max_len}
    cache = out / 'behaviour_logprobs.json'
    cached = json.loads(cache.read_text()) if cache.exists() else {}
    if cached.get('key') == key:
        mu = cached['mu']
    else:
        mu = behaviour_logprobs(model, data['train'])
        cache.write_text(json.dumps({'key': key, 'mu': mu}))
    train = [(ids, n_prompt, adv, role, m) for (ids, n_prompt, adv, role), m in zip(data['train'], mu)]
    print(json.dumps({'behaviour_logprobs_s': round(time.time() - t0, 1)}))

    n_epochs = math.ceil(a.epochs)
    orders = []
    for e in range(n_epochs):
        d = list(train)
        random.shuffle(d)
        orders.append(d if e + 1 <= a.epochs else d[:int(len(d) * (a.epochs - e))])
    total_steps = sum(math.ceil(len(o) / a.accum) for o in orders)
    params = [x for x in model.parameters() if x.requires_grad]
    opt = torch.optim.AdamW(params, lr=a.lr, weight_decay=0.0)
    sched = get_cosine_schedule_with_warmup(opt, int(a.warmup * total_steps), total_steps)
    log = open(out / 'log.jsonl', 'w')

    def record(entry):
        entry |= {'time': round(time.time() - t0, 1), 'peak_mem_gb': round(torch.cuda.max_memory_allocated() / 2**30, 2)}
        print(json.dumps(entry), flush=True)
        log.write(json.dumps(entry) + '\n')
        log.flush()

    step = 0
    record({'step': 0, 'val': evaluate(model, data['val'])})
    for epoch, order in enumerate(orders):
        for i in range(0, len(order), a.accum):
            group = order[i:i + a.accum]
            total, ratios = 0.0, []
            for ids, n_prompt, adv, _, mu_lp in group:
                lp, n = log_prob(model, ids, n_prompt)
                w = adv
                if adv < 0:
                    ratio = torch.exp(lp.detach() - mu_lp).clamp(max=1.0).item()
                    ratios.append(ratio)
                    w = adv * ratio
                loss = -w * lp / n / len(group)
                loss.backward()
                total += loss.item()
            torch.nn.utils.clip_grad_norm_(params, 1.0)
            opt.step()
            sched.step()
            opt.zero_grad(set_to_none=True)
            step += 1
            entry = {'step': step, 'epoch': epoch, 'loss': total, 'lr': sched.get_last_lr()[0],
                     'pos': sum(x[2] >= 0 for x in group), 'neg': len(ratios),
                     'neg_ratio_mean': sum(ratios) / len(ratios) if ratios else None}
            if a.eval_every and step % a.eval_every == 0:
                entry['val'] = evaluate(model, data['val'])
            if a.save_every and step % a.save_every == 0:
                model.save_pretrained(out / f'adapter_step{step}')
                entry['saved'] = f'adapter_step{step}'
            record(entry)
        end = {'step': step, 'epoch': epoch, 'val': evaluate(model, data['val'])}
        if epoch + 1 < len(orders):
            model.save_pretrained(out / f'adapter_ep{epoch + 1}')
            end['saved'] = f'adapter_ep{epoch + 1}'
        record(end)

    model.save_pretrained(out / 'adapter')
    tok.save_pretrained(out / 'adapter')
    if a.gen_check:
        model.gradient_checkpointing_disable()
        val = rows['val'] or rows['train']
        picks = [r for r in val if r['writer_role'] == 'extractor'][:a.gen_check // 2 or 1] + \
                [r for r in val if r['writer_role'] == 'manager'][:a.gen_check // 2]
        gens = generate_check(model, tok, picks, max_new_tokens=1024)
        (out / 'gen_check.jsonl').write_text(''.join(json.dumps(g, ensure_ascii=False) + '\n' for g in gens))
        record({'gen_check': [{k: g[k] for k in ('writer_role', 'complete', 'lines', 'bad_lines', 'teacher_lines')}
                              for g in gens]})
    (out / 'data_stats.json').write_text(json.dumps(stats, indent=2))


if __name__ == '__main__':
    main()
