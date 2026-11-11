# 实验结果与论文图表

每个实验一个子目录。每张图都保存四份文件：

- `<name>.pdf`：矢量图，嵌入字体，直接用于论文
- `<name>.png`：300 dpi 预览
- `<name>.csv`：图中每个数据点的数值
- `<name>.meta.json`：生成命令和来源 run 目录

需要重画时，执行 `meta.json` 里的 command 即可（`python -m memeval.figures ...`）。原始 run（predictions、scores、summary）在 `runs/`，在服务器 `~/M-locomo/runs` 上生成后同步回来。

样式约定（见 `memeval/figures.py`）：单栏宽 3.5 in，双栏宽 7.16 in；8 pt 无衬线字体；单一 y 轴；每个方法颜色固定，本文方法始终为蓝色，SimpleMem 始终为橙色；散点图只给这两者上色，其余基线为灰色，靠标记形状和直接标注区分；问题类别在所有图中使用同一顺序。

| 目录 | 内容 | 来源 |
|---|---|---|
| `pilot_dev_K10/` | 试跑：Full context 与三种 RAG 的对比，dev 集 K=10，每类 5 题，Qwen3-8B 作答，gpt-6-luna 评判（评判规则 v2） | `runs/pilot_*_K10` |
