# ShopMem and M-LoCoMo

Code and data for the paper **"ShopMem: Learning to write and read long-term memory for conversational agents in manufacturing"**.

- **ShopMem** is a trainable long-term memory method for conversational agents on the shop floor. It has two parts:
  - The **Writer** anchors facts to ISA-95 objects and production shifts. It stores each fact with a bitemporal validity interval, so an old state is closed rather than deleted. The Writer is trained with reward-filtered rejection sampling fine-tuning.
  - The **Reader** decides when to retrieve again and whether to look at earlier versions of a fact. It is trained with GRPO on a fixed memory store.
- **M-LoCoMo** is a long-conversation memory benchmark for manufacturing. Its interaction histories come from two sources: human–agent collaboration and agent tool calls. Its questions cover 13 types of typical shop-floor needs.

## Main result (M-LoCoMo test90, K = 30)

| Method | Judge accuracy (%) | Context tokens |
|---|---:|---:|
| **ShopMem (Writer + Reader)** | **90.9** | 2,092 |
| ShopMem-Writer (SFT), one-shot reading | 53.3 | 1,017 |
| RAG hybrid + rerank | 46.5 | 818 |
| Observation mask | 32.0 | 74,093 |
| Full context | 29.5 | 98,964 |
| SimpleMem | 20.1 | 3,811 |

The full table, per-category results, and ablations are in `results/test90_main_v3/`.

## Repository layout

```
memeval/            core library
  methods/shopmem_writer.py   ShopMem Writer (extractor + memory manager)
  methods/shopmem_reader.py   ShopMem Reader (multi-round retrieval)
  methods/shopmem.py          retrieval over the memory bank
  memory_ops.py               memory units, bitemporal validity, ADD / SUPERSEDE / EXPIRE
  methods/rag.py, context_baselines.py, simplemem_adapter.py   baselines
  run.py, score.py            run a method on a split; LLM-judge scoring
  reader_rl.py, sft_data.py, topr_data.py                      training data / rewards
scripts/            training (train_writer_*.py, train_reader_*.py) and experiment launch scripts
data/long/{en,zh}/  M-LoCoMo long-history splits: train / dev30 / test90, K = number of stacked scenarios
data/generated*/    single-scenario source corpus (100 scenarios, 3,900 questions per language)
generation/         dataset generators (fixed seed)
evaluation/         dataset validation, statistics, audits
schema/, ontology/  JSON Schemas and vocabularies (ISA-95-aligned entities and events)
results/            CSV / JSON behind the tables and figures in the paper
tests/              unit tests
docs/               schema spec and (Chinese) development notes and data card
```

## Installation

Python 3.12 is used.

```bash
pip install -r requirements.txt          # evaluation and inference
```

Training the Writer and Reader also needs `torch`, `transformers`, and `peft`. Serving local models needs `vllm`. See `requirements.txt` for the versions used.

## Quick start

Offline smoke test (no model or API calls):

```bash
python -m pytest -q tests
python -m memeval.run --method rag_bm25 --lang en --pool test90 --K 1 --dry-run \
    --limit-per-category 1 --max-histories 1
```

Running a method against an OpenAI-compatible endpoint (for example, a local vLLM server):

```bash
python -m memeval.run --method shopmem_writer --lang en --pool test90 --K 30 \
    --model Qwen3-4B --base-url http://localhost:8000/v1 --api-key-env "" --no-judge \
    --method-config embed_base_url=http://localhost:8006/v1 embed_model=Qwen3-Embedding-8B
python -m memeval.score runs/<run_name> --judge-model <judge> \
    --base-url $JUDGE_BASE_URL --api-key-env JUDGE_API_KEY
```

Available methods: `full_context`, `observation_mask`, `sliding_window`, `rag_bm25`, `rag_dense`, `rag_hybrid`, `simplemem`, `shopmem`, `shopmem_writer`, `shopmem_reader`.

The launch scripts in `scripts/` record the exact settings used for the paper. They assume local vLLM servers and a model directory `models/`, so ports and paths may need to be adapted. API keys are read from environment variables and are never stored in the code.

Backbone models: Qwen3-4B (Writer), Qwen3-8B (Reader), Qwen3-Embedding-8B, and Qwen3-Reranker-4B. Trained adapters are not included in this repository.

## Data

All M-LoCoMo content is synthetic. Equipment, identifiers, materials, and parameters are fictional and do not come from any real factory. The content is not engineering guidance. Statistics are in `data/long/STATS.md`, and the field specification is in `docs/SCHEMA.md`.

## License

- Code: MIT License (`LICENSE`).
- Data (`data/`): CC BY 4.0 (`LICENSE-DATA`).

## Citation

```bibtex
@article{shopmem,
  title   = {ShopMem: Learning to write and read long-term memory for conversational agents in manufacturing},
  note    = {Manuscript under review}
}
```
