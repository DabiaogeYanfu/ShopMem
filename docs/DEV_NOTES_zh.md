# M-LoCoMo Schema v0.1

制造领域长对话记忆评测集（Manufacturing Long-term Conversational Memory）。本仓库提供一个与 LoCoMo/LongMemEval 对话评测思路兼容、并在语义上对齐 ISA-95、OPC UA 信息建模和 ISO 23247 时间线概念的最小可复现样例。

## 当前内容

- `schema/`：JSON Schema，约束场景、会话、事件、记忆单元和问题格式。
- `ontology/`：实体、事件、关系和状态词表。
- `data/`：设备维护和质量异常样例（均为合成数据）。
- `evaluation/`：不依赖外部服务的词法检索基线、跨文件引用校验与指标计算。
- `docs/SCHEMA.md`：字段规范、证据协议和数据划分规则。

## 快速运行

```bash
python -m evaluation.run_baseline --data-dir data/dev --top-k 5
python -m evaluation.validate_dataset
```

## 生成 3900 条问题样本（v0.2）

```bash
python generation/generate_dataset.py          # 中文 -> data/generated/
python generation/generate_dataset_en.py       # 英文 -> data/generated-EN/
python -m evaluation.validate_dataset --data-dir data/generated/test
python evaluation/check_generated.py           # 班次/指代/不可回答题的语义一致性检查
```

脚本固定随机种子 `20260914`，生成 100 个场景、500 个 session、500 个事件、500 个长期记忆单元、100 个工作记忆快照和 3900 个问题（13 类 × 3 题 × 100 场景），并按场景数 80/10/10 划分 train/dev/test（对应 3120/390/390 个问题）。所有划分按场景和任务隔离。v0.1 数据备份在 `_backup_v0.1/`。

v0.2 相对 v0.1 的变化：

- **生产日历**：每个 site 有自己的班次日历（SITE_01–03 三班 07:30/15:30/23:30，SITE_04–05 两班 08:00/20:00），写在 `data/generated*/production_calendar.json`；session 带 `site_id`，开始时间随机落在某个班次内。阶段 0/2/3 的 `user_query` 总会说明事件发生在“本班”还是“上一班”，并带 `resolved_time` / `resolved_shift` 真值。
- **新类别 `shift_temporal`**：问事件发生在哪天哪个班次。班次边界不在零点，夜班跨日，所以必须结合日历和说话时刻才能换算正确。
- **新类别 `unanswerable`**（`answerable=false`）：询问从未出现的相似编号、对话从未记录的属性（供应商、费用、客户），或带错误前提（“第三次调整”）的问题。
- **跨轮指代**：阶段 2–4 的高密度报告约 40% 改用“这台设备 / 它 / 这一批”，实体只在前一条低密度轮次中出现。
- 工具返回里的检测时间与发起查询的轮次时间一致；报警触发时间等于事件发生时间。
- 任务约束和待办改为在对话中说出（v0.1 中它们只存在于结构化文件里，`constraint_recall` 无法从历史作答）。
- `difficulty` 改为按类别确定（v0.1 按题号轮转，没有实际含义）。

每个场景的 5 个 session 里，1 个是人与人的假设讨论协作对话，其余 4 个是用户与工业 Agent 的 ReAct 式诊断 episode（`user_query` -> `agent_think` -> 多次 `agent_tool_call` 排除正常项 -> 恰好 1 次高密度 `agent_tool_call` 命中关键证据 -> `agent_response` 综合结论），用于评测语义门控能否丢弃 Agent 自身的中间推理和例行工具返回、同时精确保留那一条真正带来结论的证据。

样例数据刻意包含跨会话时间关系、状态更新、失败干预、角色冲突和旧事实覆盖，用于验证记忆系统是否真正利用长期上下文。

## 长历史版本 M-LoCoMo-Long

单个场景只有约 1.7k tokens，无法体现长期记忆的必要性。`generation/build_long_history.py` 将 K 个场景按 session 时间交织为一条车间级交互历史，每题需在含 K-1 个干扰场景（同模板、不同实体）的历史中作答：

```bash
uv venv .venv && uv pip install --python .venv/bin/python tiktoken
.venv/bin/python generation/build_long_history.py      # -> data/long/<zh|en>/<eval|dev>/K<k>/
.venv/bin/python evaluation/dataset_stats.py           # -> data/long/STATS.md, stats.json
```

- `eval` 池 = train + test（90 场景，3510 题），K ∈ {1, 10, 30, 90}，每历史约 1.8k / 18k / 53k / 159k tokens（中文）；`dev` 池（10 场景，390 题）只用于调参。
- 分组按领域分层、固定种子；session 整体按 `(session_start, session_id)` 排序，原始时间戳不做平移（对话文本中引用了具体钟点）。
- `histories.jsonl` 中 session 保留 `scenario_id` 仅供分析，记忆系统写入时不得读取该字段。`site_id` 可以读取，用于查 `production_calendar.json`（相当于 MES 中的班次日历）。

## 评测框架 memeval

`memeval/` 为所有记忆方法提供统一的写入、检索、作答和评判流程：

```bash
uv pip install --python .venv/bin/python openai pytest
.venv/bin/python -m pytest tests -q                                   # 框架自测
.venv/bin/python -m memeval.run --method full_context --lang zh --pool dev --K 10 --dry-run --limit-per-category 2
.venv/bin/python -m memeval.run --method observation_mask --lang zh --pool eval --K 30 \
    --model qwen-plus --base-url https://dashscope.aliyuncs.com/compatible-mode/v1 \
    --api-key-env DASHSCOPE_API_KEY --judge-model qwen-max
.venv/bin/python -m memeval.score runs/<run-name> --judge-model ...   # 换评判模型重评
```

- **方法能看到什么**：`History` / `Query` 只包含交互日志里本来就有的信息（文本、说话人、时间戳、`turn_type`、`tool_name`、`site_id`）。门控真值、指代解析真值、`mentions`、`scenario_id` 都放在 `Gold` 中，只有 `uses_oracle=True` 的基线能读取。原始 ID 中含有场景编号（`SC_GEN_0003_S01_D02`、`eng0003`），所以每条历史都换成不透明 ID：`T00001` / `S0001`、统一的 `AGENT`、按系统区分的 `SYS_EMS` / `SYS_MES`，人员随机编号 `P###`。
- **统一的作答**：所有方法使用同一个回答 prompt（`memeval/prompts.py`），其中包含当前时间、生产日历和班次命名约定。评判规则按类别区分：通用、`shift_temporal`、`unanswerable`。
- **新增方法**：继承 `memeval/methods/base.py` 中的 `MemoryMethod`，实现 `ingest(history)` 和 `retrieve(query) -> Retrieved(context, provenance)`，在 `methods/__init__.py` 中注册。写入阶段的 LLM 调用须带 `stage='write'`，这样写入成本才能单独统计。`provenance` 是上下文所依据的轮次 ID，用于计算证据召回。
- **已有基线**：`full_context`、`sliding_window`、`observation_mask`、`oracle_gate`（读取 Gold，只作参考上界）；检索类 `rag_dense`（Qwen3-Embedding 余弦相似度）、`rag_bm25`（jieba 分词 + 停用词，ID 作为整体 token）、`rag_hybrid`（两者 RRF 融合，k=60）、`oracle_gate_rag`（真值门控后做 hybrid 检索）。检索类参数：`--method-config top_k=10 unit=turn|chunk chunk_turns=4 embed_base_url=http://localhost:8002/v1`；`embed_base_url=fake` 用于离线测试。向量缓存在 `.cache/embeddings.sqlite`。
- **不接评判也能打分**：`--no-judge` 仍会输出 F1、BLEU-1、证据召回和效率指标，只有 Judge 准确率留空。之后接入评判模型时，用 `memeval.score` 补评即可。
- **输出**（`runs/<run-name>/`）：`predictions.jsonl`（逐题追加，中断后可续跑）、`histories.jsonl`（写入耗时、记忆规模、写入 token）、`usage.json`、`scores.jsonl`、`summary.json` / `summary.md`（分类别的 Judge 准确率、F1、BLEU-1、证据召回（轮次级 / 事件级）、上下文 token、效率）。LLM 调用缓存在 `.cache/llm.sqlite`。
- **本地模型**：在实验室服务器上用 `scripts/serve_vllm.sh` 启动 vLLM 的 OpenAI 兼容服务，用 `scripts/stop_vllm.sh` 停止，只使用 3–7 号卡。Qwen3-8B 需要两卡并行，并用 YaRN 扩展到 128k 上下文（通过 `--hf-overrides` 传入 rope_scaling）。客户端参数为 `--base-url http://localhost:8001/v1 --api-key-env "" --extra-body '{"chat_template_kwargs": {"enable_thinking": false}}'`。
- **token 口径**：`context_tokens` 用 tiktoken o200k 计数，`answer_prompt_tokens` 是模型 tokenizer 的实际数字。中文下 Qwen tokenizer 的计数约多 27%（K=30：69.6k 对 88.1k）。设置 `max_context_tokens` 时要按模型窗口留出余量。
- **渲染格式**：harness 渲染的每轮格式是 `[时间] 说话人 (角色, 类型): 文本`，比 `data/long/STATS.md` 使用的格式更长，dev K=10 的完整上下文约为 22.7k 对 17.1k tokens。论文中报告的 token 成本以 harness 的数字为准。

## 数据声明

当前文件全部为合成数据，不代表任何真实设备、参数或操作规程，不能用于控制或维修真实设备。正式发布前应补充领域专家审核、数据卡、许可证和 SHA-256 清单。
