"""GRPO for ShopMem-Reader (Qwen3-8B LoRA), 方法论_v3_大纲 §3.4.3 step 2; 实验计划_v3 stage R2.

Runs on autoDL-B with two GPUs: vLLM serves the policy on one (started by scripts/run_reader_grpo.sh,
runtime LoRA loading on), this script trains on the other. Per step:
1. the current adapter is saved and loaded into vLLM under a new name (step<k>);
2. pool questions (memeval.reader_rl pool: G answers of the SFT Reader not all equal) go to the rollout
   service on lab (memeval.reader_rl serve, through an SSH tunnel), which plays --G-sample episodes per
   question with that adapter (real searches) and returns them with rewards r = r_task + λ_f·r_fmt + r_len
   and, per Reader reply, the exact prompt / sampled token ids and vLLM's log-probabilities;
3. dynamic sampling (DAPO): a group whose rewards are all equal has Â = 0 and is dropped; more questions
   are requested (up to --max-calls per step) until the step has --batch-questions groups with signal;
4. oversample and select: Â_i = (r_i − mean) / (std + eps) over all --G-sample episodes of the group (a
   better baseline; --adv-norm none drops the std, Dr. GRPO, so a lone wrong answer in a mostly-correct
   group no longer gets Â ≈ −4), then only --G-train of them are trained on, the ones with the most extreme rewards
   (half from the top, half from the bottom), so the update is spent where the rewards differ; with
   --select-weight the selected positive Â are rescaled to the whole group's positive Â mass and the negative
   ones to its negative mass, so the selected episodes stand in for the whole group and their Â sum to 0
   (unweighted, a 15/16 group trains its one wrong answer with only 7 of the 15 right ones: Â sum −2.0);
5. --minibatches optimiser steps on the GRPO objective of §3.4.3 with these changes:
   - clip-higher (DAPO): clip(w, 1 − ε_low, 1 + ε_high), ε_high > ε_low leaves room for low-probability
     tokens to rise (against entropy collapse);
   - token-level normalisation (DAPO): the loss is summed over every trained reply token of the
     minibatch and divided by their number, so a long reply does not dilute its tokens' gradient
     (length is kept in check by r_len in the reward instead);
   - training-inference mismatch: the trainer scores the token ids vLLM sampled (no re-tokenisation);
     a token whose probability under the trainer (π_old, before the update) and under vLLM differ by more
     than a factor of --mismatch (ratio outside [1/m, m]) is masked out of the loss; with --tis-cap C the
     remaining tokens' policy-gradient term is also weighted by min(π_old/π_vllm, C) (truncated IS);
   - KL_t = exp(ref − lp) − (ref − lp) − 1 (k3) against ρ_ref = the SFT Reader, weight β.
   w = π_φ / π_old per token; π_old log-probs are computed once before the first minibatch.
Adapters: adapter_step<k> every --save-every steps and the final adapter. log.jsonl per step: reward,
accuracy, format_ok, search share, answer tokens, groups (requested / with signal), token entropy,
mismatch-masked share, mean |log π_old − log π_vllm| and share > 0.1, KL, clip shares (low / high), loss, timings.

    python scripts/train_reader_grpo.py --pool-file runs/reader_grpo_pool/pool.json \
        --init-adapter runs/reader_sft_sol_v3_vote4/adapter --out runs/reader_grpo1 \
        --model /root/autodl-tmp/models/Qwen3-8B --vllm-url http://localhost:18100 \
        --rollout-url http://localhost:18120/rollout
"""
import argparse
import json
import math
import random
import shutil
import statistics
import time
import urllib.error
import urllib.request
from pathlib import Path

import torch
from peft import PeftModel
from transformers import AutoModelForCausalLM, AutoTokenizer, get_constant_schedule_with_warmup


def post(url, body, timeout):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = r.read()
    try:
        return json.loads(body or b'{}')
    except json.JSONDecodeError:  # vLLM's load/unload_lora_adapter answer 200 with plain text ("Success: ...")
        return {'text': body.decode(errors='replace')}


# errors of an external API (the luna judge relay) that are worth waiting out: the rollout is retried until the
# relay is back (--wait-hours), not given up after a few tries
TRANSIENT = ('APIConnectionError', 'APITimeoutError', 'RateLimitError', 'InternalServerError', 'Connection error')


def post_retry(url, body, timeout, tries=4, wait=60, wait_hours=12):
    """post() for the rollout service: an HTTP 500 (its JSON body carries the error) or a dropped connection
    is printed and retried after `wait` s; the same request (questions, seed, call) is sent again, so a retry
    replays the same episodes (judge verdicts from the cache). Other errors: up to tries-1 retries; an
    external-API outage (TRANSIENT, e.g. the luna relay unreachable): retried for up to wait_hours."""
    t0, k = time.time(), 0
    while True:
        k += 1
        try:
            return post(url, body, timeout)
        except urllib.error.HTTPError as e:
            err = e.read().decode(errors='replace')[:2000]
        except (urllib.error.URLError, ConnectionError, TimeoutError) as e:
            err = f'{type(e).__name__}: {e}'
        transient = any(t in err for t in TRANSIENT)
        waited_h = (time.time() - t0) / 3600
        print(f'rollout call failed (try {k}, {"external API outage, waiting" if transient else f"of {tries}"}, '
              f'{waited_h:.2f} h so far): {err}', flush=True)
        if (not transient and k >= tries) or waited_h >= wait_hours:
            raise RuntimeError(f'rollout service failed (try {k}, {waited_h:.2f} h): {err}')
        time.sleep(wait if not transient else 300)


def token_logprobs(model, ids, n_prompt, entropy=False):
    """Log-probabilities of the reply tokens (1-D tensor), and their mean entropy if asked."""
    x = torch.tensor([ids], device=model.device)
    inner = model.get_base_model()
    h = inner.model(input_ids=x).last_hidden_state[0, n_prompt - 1:-1]
    logp = torch.log_softmax(inner.lm_head(h).float(), -1)
    lp = logp.gather(1, x[0, n_prompt:, None])[:, 0]
    if not entropy:
        return lp
    with torch.no_grad():
        ent = -(logp.exp() * logp).sum(-1).mean().item()
    return lp, ent


def group_advantages(rows, eps, norm='std'):
    """Â per row over its whole question group (divided by the group std if norm == 'std');
    returns {question_id: rows}."""
    groups = {}
    for r in rows:
        groups.setdefault(r['question_id'], []).append(r)
    for g in groups.values():
        rs = [r['reward'] for r in g]
        m, s = statistics.mean(rs), statistics.pstdev(rs)
        for r in g:
            r['adv'] = 0.0 if s == 0 else (r['reward'] - m) / (s + eps if norm == 'std' else 1.0)
    return groups


def select_extremes(group, k):
    """The k episodes with the most extreme rewards: ceil(k/2) highest, floor(k/2) lowest (ties by index)."""
    if len(group) <= k:
        return list(group)
    by_r = sorted(group, key=lambda r: (r['reward'], r['i']))
    return by_r[:k // 2] + by_r[len(by_r) - (k - k // 2):]


def selection_weights(group, chosen):
    """Per chosen episode: (group's Â mass of its sign) / (chosen Â mass of its sign), times len(chosen) / len(group)
    so the update keeps the size of an unweighted one (the chosen stand in for the group's mean, not its sum)."""
    mass = lambda rows, sign: sum(r['adv'] for r in rows if r['adv'] * sign > 0)
    f = len(chosen) / len(group)
    return [f * mass(group, 1 if r['adv'] > 0 else -1) / mass(chosen, 1 if r['adv'] > 0 else -1) if r['adv'] else 1.0
            for r in chosen]


def samples(row, max_len):
    """One (ids, n_prompt, vllm logprobs) per Reader reply, from the token ids vLLM rendered and sampled;
    None if any reply is too long (the episode is then skipped whole)."""
    out = []
    for t in row['turns']:
        ids = t['prompt_ids'] + t['ids']
        if len(ids) > max_len:
            return None
        out.append((ids, len(t['prompt_ids']), torch.tensor(t['logprobs'])))
    return out


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--pool-file', required=True, help='pool.json of memeval.reader_rl pool (question_ids)')
    p.add_argument('--init-adapter', required=True, help='the SFT Reader: initial policy and KL reference')
    p.add_argument('--out', required=True)
    p.add_argument('--model', default='/root/autodl-tmp/models/Qwen3-8B')
    p.add_argument('--vllm-url', default='http://localhost:18100')
    p.add_argument('--rollout-url', default='http://localhost:18120/rollout')
    p.add_argument('--G-sample', type=int, default=16, help='episodes sampled per question (advantage baseline)')
    p.add_argument('--G-train', type=int, default=8, help='of them, the most extreme ones trained on')
    p.add_argument('--batch-questions', type=int, default=16, help='groups with signal per step')
    p.add_argument('--max-calls', type=int, default=3, help='rollout requests per step (dynamic sampling)')
    p.add_argument('--epochs', type=float, default=1)
    p.add_argument('--max-steps', type=int, default=0, help='stop after this many steps (0: until the pool is used)')
    p.add_argument('--lr', type=float, default=1e-6)
    p.add_argument('--warmup-steps', type=int, default=3)
    p.add_argument('--beta', type=float, default=0.001)
    p.add_argument('--clip-low', type=float, default=0.2)
    p.add_argument('--clip-high', type=float, default=0.28)
    p.add_argument('--mismatch', type=float, default=2.0, help='mask tokens with π_train/π_vllm outside [1/m, m]')
    p.add_argument('--adv-eps', type=float, default=1e-6)
    p.add_argument('--adv-norm', choices=['std', 'none'], default='std',
                   help='std: (r − mean) / std (GRPO); none: r − mean (Dr. GRPO)')
    p.add_argument('--select-weight', action='store_true',
                   help='rescale the selected episodes positive / negative Â to the group positive / negative mass')
    p.add_argument('--tis-cap', type=float, default=0.0,
                   help='weight the policy-gradient term by min(π_old/π_vllm, cap) per token (0: off)')
    p.add_argument('--minibatches', type=int, default=2, help='optimiser steps per rollout batch')
    p.add_argument('--max-len', type=int, default=8192)
    p.add_argument('--save-every', type=int, default=10)
    p.add_argument('--rollout-timeout', type=int, default=3600)
    p.add_argument('--seed', type=int, default=0)
    p.add_argument('--resume', action='store_true', help='continue from <out>/state.pt and the last adapter_step<k> '
                   '(optimizer, scheduler, question queue, RNG states are saved with every adapter_step<k>)')
    a = p.parse_args()

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / ('args_resume.json' if a.resume else 'args.json')).write_text(json.dumps(vars(a), indent=2))
    random.seed(a.seed)
    torch.manual_seed(a.seed)

    qids = json.loads(Path(a.pool_file).read_text())['question_ids']
    order = []
    for e in range(math.ceil(a.epochs)):
        q = list(qids)
        random.shuffle(q)
        order += q if e + 1 <= a.epochs else q[:int(len(q) * (a.epochs - e))]
    queue = list(reversed(order))  # pop() takes the next question

    tok = AutoTokenizer.from_pretrained(a.model)
    model = AutoModelForCausalLM.from_pretrained(a.model, dtype=torch.bfloat16, device_map={'': 0},
                                                 attn_implementation='sdpa')
    model.config.use_cache = False
    model.gradient_checkpointing_enable(gradient_checkpointing_kwargs={'use_reentrant': False})
    model.enable_input_require_grads()
    state = torch.load(out / 'state.pt', weights_only=False) if a.resume else None
    start = out / f"adapter_step{state['step']}" if state else a.init_adapter
    model = PeftModel.from_pretrained(model, start, is_trainable=True)  # adapter 'default'
    model.load_adapter(a.init_adapter, adapter_name='ref', is_trainable=False)
    model.set_adapter('default')
    for m in model.modules():  # no dropout: π/π_old and the KL compare policies, not dropout noise
        if isinstance(m, torch.nn.Dropout):
            m.p = 0.0
    params = [x for n, x in model.named_parameters() if x.requires_grad]
    assert params and all('.default.' in n for n, x in model.named_parameters() if x.requires_grad)
    opt = torch.optim.AdamW(params, lr=a.lr, weight_decay=0.0)
    sched = get_constant_schedule_with_warmup(opt, a.warmup_steps * a.minibatches)
    step = 0
    if state:
        opt.load_state_dict(state['opt'])
        sched.load_state_dict(state['sched'])
        queue, step = state['queue'], state['step']
        random.setstate(state['py_rng'])
        torch.set_rng_state(state['torch_rng'])
        print(f'resumed at step {step} from {start}, {len(queue)} questions left', flush=True)
    log = open(out / 'log.jsonl', 'a')
    live = out / 'live'
    t0, served = time.time(), None

    def record(entry):
        entry |= {'time': round(time.time() - t0, 1), 'peak_mem_gb': round(torch.cuda.max_memory_allocated() / 2**30, 2)}
        print(json.dumps(entry), flush=True)
        log.write(json.dumps(entry) + '\n')
        log.flush()

    def serve_adapter(name):
        """Save the current adapter and load it into vLLM as `name`; unload the previous one."""
        nonlocal served
        d = live / name
        model.save_pretrained(d, selected_adapters=['default'])
        post(f'{a.vllm_url}/v1/load_lora_adapter', {'lora_name': name, 'lora_path': str(d.resolve())}, 600)
        if served:
            post(f'{a.vllm_url}/v1/unload_lora_adapter', {'lora_name': served}, 600)
            shutil.rmtree(live / served, ignore_errors=True)
        served = name

    def score_old_and_ref(batch):
        """π_old (the current adapter, with entropy) and ρ_ref log-probs of every trained reply; tokens whose
        π_old and vLLM probabilities disagree beyond --mismatch get mask 0; with --tis-cap also the truncated
        IS weight min(π_old/π_vllm, cap) per token."""
        ents, masked, total, absdiff, over = [], 0, 0, 0.0, 0
        with torch.no_grad():
            model.eval()
            for ep in batch:
                ep['old'], ep['mask'], ep['tis'] = [], [], []
                for ids, n, vllm_lp in ep['samples']:
                    lp, ent = token_logprobs(model, ids, n, entropy=True)
                    ents.append(ent)
                    diff = lp.cpu() - vllm_lp
                    absdiff += diff.abs().sum().item()
                    over += int((diff.abs() > 0.1).sum())
                    mask = (diff.abs() <= math.log(a.mismatch)).float().to(lp.device)
                    ep['tis'].append(diff.exp().clamp(max=a.tis_cap).to(lp.device) if a.tis_cap else None)
                    masked += int((mask == 0).sum())
                    total += len(mask)
                    ep['old'].append(lp)
                    ep['mask'].append(mask)
            model.set_adapter('ref')
            for ep in batch:
                ep['ref'] = [token_logprobs(model, ids, n) for ids, n, _ in ep['samples']]
            model.set_adapter('default')  # also makes 'default' trainable again
            model.train()
        return {'entropy': statistics.mean(ents), 'mismatch_masked': masked / max(total, 1),
                'mismatch_absdiff_mean': absdiff / max(total, 1), 'mismatch_over_0.1': over / max(total, 1)}

    while queue and (not a.max_steps or step < a.max_steps):
        step += 1
        t_step = time.time()
        serve_adapter(f'step{step - 1}')
        rows, groups, calls, requested = [], {}, 0, 0
        while queue and calls < a.max_calls:
            need = a.batch_questions - sum(len({r['reward'] for r in g}) > 1 for g in groups.values())
            if need <= 0:
                break
            ask = [queue.pop() for _ in range(min(len(queue), need if calls == 0 else math.ceil(need * 1.5)))]
            resp = post_retry(a.rollout_url, {'adapter': served, 'question_ids': ask, 'G': a.G_sample, 'seed': step,
                                        'call': calls}, a.rollout_timeout)
            if 'error' in resp:
                raise RuntimeError(f'rollout service: {resp["error"]}')
            calls += 1
            requested += len(ask)
            rows += resp['rows']
            groups = group_advantages(rows, a.adv_eps, a.adv_norm)
        t_roll = time.time() - t_step
        signal = [g for g in groups.values() if len({r['reward'] for r in g}) > 1]
        batch, too_long = [], 0
        for g in signal:
            chosen = select_extremes(g, a.G_train)
            weights = selection_weights(g, chosen) if a.select_weight else [1.0] * len(chosen)
            for r, wt in zip(chosen, weights):
                s = samples(r, a.max_len)
                if s is None:
                    too_long += 1
                    continue
                batch.append({'adv': r['adv'] * wt, 'samples': s})
        entry = {'step': step, 'episodes': len(rows), 'reward': statistics.mean(r['reward'] for r in rows),
                 'acc': statistics.mean(bool(r['correct']) for r in rows),
                 'format_ok': statistics.mean(bool(r['format_ok']) for r in rows),
                 'searched': statistics.mean(bool(r['searches']) for r in rows),
                 'searches_mean': statistics.mean(len(r['searches']) for r in rows),
                 'answer_tokens_mean': statistics.mean(r['answer_tokens'] for r in rows),
                 'len_penalised': statistics.mean(r['r_len'] < 0 for r in rows),
                 'reply_tokens_mean': statistics.mean(sum(len(t['ids']) for t in r['turns']) for r in rows),
                 'truncated_replies': sum(t['finish'] == 'length' for r in rows for t in r['turns']),
                 'groups_requested': requested, 'groups_signal': len(signal), 'rollout_calls': calls,
                 'trained_episodes': len(batch), 'too_long': too_long,
                 'adv_pos_sum': sum(max(b['adv'], 0) for b in batch),
                 'adv_neg_sum': sum(min(b['adv'], 0) for b in batch), 'questions_left': len(queue),
                 'rollout_s': round(t_roll, 1)}
        if batch:
            entry |= score_old_and_ref(batch)
            random.shuffle(batch)
            mbs = [batch[i::a.minibatches] for i in range(a.minibatches) if batch[i::a.minibatches]]
            losses, kl_sum, clip_lo, clip_hi, ntok = [], 0.0, 0, 0, 0
            for mb in mbs:
                T = sum(m.sum().item() for ep in mb for m in ep['mask'])
                loss_mb = 0.0
                for ep in mb:
                    for k, (ids, n, _) in enumerate(ep['samples']):
                        mask = ep['mask'][k]
                        lp = token_logprobs(model, ids, n)
                        w = torch.exp(lp - ep['old'][k])
                        pg = torch.min(w * ep['adv'], w.clamp(1 - a.clip_low, 1 + a.clip_high) * ep['adv'])
                        if ep['tis'][k] is not None:
                            pg = pg * ep['tis'][k]
                        d = ep['ref'][k] - lp
                        kl = torch.exp(d) - d - 1
                        loss = -((pg - a.beta * kl) * mask).sum() / max(T, 1)
                        loss.backward()
                        loss_mb += loss.item()
                        kl_sum += (kl.detach() * mask).sum().item()
                        clip_lo += ((w < 1 - a.clip_low) & (mask > 0)).sum().item()
                        clip_hi += ((w > 1 + a.clip_high) & (mask > 0)).sum().item()
                        ntok += int(mask.sum().item())
                torch.nn.utils.clip_grad_norm_(params, 1.0)
                opt.step()
                sched.step()
                opt.zero_grad(set_to_none=True)
                losses.append(loss_mb)
            entry |= {'loss': statistics.mean(losses), 'kl_per_token': kl_sum / max(ntok, 1),
                      'clip_low_frac': clip_lo / max(ntok, 1), 'clip_high_frac': clip_hi / max(ntok, 1),
                      'trained_tokens': ntok, 'lr': sched.get_last_lr()[0]}
        if a.save_every and step % a.save_every == 0:
            model.save_pretrained(out / f'adapter_step{step}', selected_adapters=['default'])
            torch.save({'step': step, 'queue': queue, 'opt': opt.state_dict(), 'sched': sched.state_dict(),
                        'py_rng': random.getstate(), 'torch_rng': torch.get_rng_state()}, out / 'state.pt.tmp')
            (out / 'state.pt.tmp').replace(out / 'state.pt')
            entry['saved'] = f'adapter_step{step}'
        entry['step_s'] = round(time.time() - t_step, 1)
        record(entry)

    model.save_pretrained(out / 'adapter', selected_adapters=['default'])
    tok.save_pretrained(out / 'adapter')
    record({'done': True, 'steps': step})


if __name__ == '__main__':
    main()
