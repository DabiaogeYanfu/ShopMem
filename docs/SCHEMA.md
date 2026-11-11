# M-LoCoMo Schema v0.1

每行一个 JSON 对象（JSONL）。`scenario_id` 是所有文件的连接键；`dialog_id` 是证据定位的最小单位。

## 六类文件

| 文件 | 作用 |
|---|---|
| `scenarios.jsonl` | 制造层级、参与对象、时间范围 |
| `sessions.jsonl` | 原始多会话对话，兼容 LoCoMo 的 session/turn 结构 |
| `events.jsonl` | 事件、状态前后值、时间和证据 |
| `memory_units.jsonl` | 可压缩、合并和检索的规范记忆 |
| `questions.jsonl` | 问题、答案、类别、证据和必需实体/事件 |
| `working_memory.jsonl` | 任务时刻的活动事实、待办项、约束和来源 |

时间采用 ISO-8601，建议带 `+08:00`；事件时间（发生）、有效时间（事实生效）和记录时间（被记录）分开。`status` 取 `active`、`historical`、`superseded`、`uncertain`。实体层级采用 enterprise/site/area/work_center/work_unit/equipment/component，并允许 material_lot、work_order、quality_test 等运营对象。

## Turn 级标注（sessions.jsonl）

每个 turn 可选携带：`turn_type`（`user_query` | `agent_think` | `agent_tool_call` | `tool_observation` | `agent_response` | `human` | `tool_call`）、`tool_name`（`agent_tool_call` 与 `tool_observation` 使用，标识被调用的检测/查询工具，如 `vibration_query`）、`is_low_density`（bool，真值标签：是否属于应被语义门控丢弃的低信息密度内容，如例行状态返回、Agent 的排查性 think、寒暄、重复确认）、`resolved_time` / `resolved_entities`（当 `text` 含相对时间表达如“刚才”“上一班”或模糊指代如“它”“这台设备”“这一批”时，给出对应的绝对时间/具体实体，作为去线性化转换的评测素材）。这些字段是可选的加性扩展，手写的 `data/dev` 样例未标注时按 `is_low_density=false`、`turn_type=human` 处理。

v0.2 起：session 带 `site_id`，scenario 带 `production_calendar_id`，对应 `production_calendar.json` 中的班次日历。含班次表达（“本班”“上一班”）的 turn 另带 `resolved_shift`，格式为 `"<班次开始日期> <班次名>"`。跨零点的班次按开始日期计，例如 `2026-04-04 夜班` 覆盖 04-04 20:00 至 04-05 08:00。event 带 `shift_id`。

`user_query`/`agent_think`/`agent_tool_call`/`agent_response` 刻画的是人与工业 Agent 的 ReAct 式交互（`speaker_role=industrial_agent`）：用户向 Agent 提出诊断/核实请求，Agent 先给出排查计划（`agent_think`），再依次调用工具排除若干正常项（`agent_tool_call`，低密度），直到恰好一次工具调用返回真正解释情况的关键数据（`agent_tool_call`，高密度），最后由 `agent_response` 综合给出结论。`human` / `tool_call` 仍保留给残留的人际协作阶段（同事之间讨论怀疑对象）使用。

v0.5 起，每次工具使用拆成两轮：`agent_tool_call` 是 Agent 发出的 JSON 请求（`{"tool", "args"}`，低密度），`tool_observation` 是工厂系统（`speaker_role=system_tool`）返回的 JSON 记录（`request_id`、`tool`、`status`、`target`、`measured_at`、`data`、`limits`、`result`、`data_quality`、`message`）。例行排查的观测全部正常、低密度；每个 episode 恰有一条关键观测为高密度，其 `measured_at` 是被报告事件的测量时间，`message` 是该观测的一句话摘要，即 tool_evidence_recall 的标准答案。两轮都带 `tool_name`。

## 问题协议

每题必须满足：答案依赖历史会话；`evidence` 指向真实 `dialog_id`；`required_events` 覆盖推理链；涉及当前状态时明确截止时间；若证据不足，标准答案允许 `uncertain`。

`answerable`（bool）标记历史中是否存在答案。`answerable=false` 的题 `evidence` 和 `required_events` 为空，`answer.aux.unanswerable_type` 取 `unseen_entity` | `unrecorded_attribute` | `false_premise`，正确回应是指出没有相关记录。

## 划分

正式版本按产线/设备族/事件模式分组切分，避免同一实体组合泄漏到测试集。当前样例仅用于开发验证。
