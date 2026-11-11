| Method | Single-hop | State | Constraint | Failed interv. | Superseded | Measures | Temporal order | Multi-hop | Cross-role | Working mem. | Tool evidence | Shift | Unanswerable | Macro | Tokens/q | Write calls | Write tok (M) | Query LLM calls/q | Δ ours [95% CI] | p |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| ShopMem (ours) | 53.7 | 42.2 | **93.3** | 47.0 | 27.0 | 9.6 | 44.4 | 0 | 0.4 | **46.7** | 28.5 | **48.9** | 88.5 | **40.8** | 613 | 823 | 1.32 | 1.0 | – | – |
| RAG-hybrid + rerank | 71.5 | 55.2 | 78.1 | **55.6** | **32.2** | **12.6** | **48.5** | 0.7 | **13.7** | 8.9 | 21.1 | 14.1 | 88.1 | 38.5 | 700 | 0 | 0.0 | 0.0 | +2.3 [0.6, 4.0] | 0.0038 |
| RAG-hybrid | **73.7** | 50.4 | 85.2 | 54.1 | 30.7 | 9.3 | 35.2 | **2.2** | 6.3 | 11.5 | 24.8 | 13.0 | 90.4 | 37.4 | 708 | 0 | 0.0 | 0.0 | +3.4 [1.7, 5.0] | 0.0 |
| RAG-dense | 61.1 | 51.9 | 81.9 | 51.5 | 24.1 | 11.5 | 39.6 | 1.1 | 4.4 | 5.2 | 9.6 | 15.2 | 86.7 | 34.1 | 757 | 0 | 0.0 | 0.0 | +6.7 [5.0, 8.3] | 0.0 |
| RAG-BM25 | 43.0 | 1.5 | 57.8 | 54.1 | 17.0 | 3.0 | 12.2 | 0 | 0.4 | 23.7 | 17.0 | 9.6 | 90.7 | 25.4 | 705 | 0 | 0.0 | 0.0 | +15.4 [13.8, 17.1] | 0.0 |
| Full context | 55.9 | 42.2 | 68.5 | 54.1 | 25.6 | 4.1 | 16.3 | 0 | 0.7 | 40.7 | **32.2** | 10.0 | 91.9 | 34.0 | 70,009 | 0 | 0.0 | 0.0 | +6.8 [5.0, 8.5] | 0.0 |
| Full context (reasoning) | 66.7 | 44.8 | 65.6 | 31.9 | 17.0 | 3.0 | 23.7 | 0 | 1.1 | 16.3 | 21.5 | 11.9 | 65.2 | 28.3 | 70,009 | 0 | 0.0 | 0.0 | +12.5 [10.7, 14.2] | 0.0 |
| Obs. mask | 62.6 | **65.9** | 46.3 | 33.7 | 16.3 | 4.1 | 31.1 | 0 | 0 | 20.7 | 3.0 | 5.2 | 65.6 | 27.3 | 48,650 | 0 | 0.0 | 0.0 | +13.5 [11.8, 15.2] | 0.0 |
| Sliding window (= ShopMem tokens) | 0 | 1.9 | 0 | 0 | 0 | 0 | 0 | 0 | 0.4 | 0.7 | 0.4 | 0 | **99.6** | 7.9 | 596 | 0 | 0.0 | 0.0 | +32.9 [31.5, 34.2] | 0.0 |
| SimpleMem | 18.5 | 5.9 | 11.9 | 9.3 | 2.2 | 0 | 4.8 | 0 | 0.7 | 2.2 | 4.4 | 3.3 | 95.2 | 12.2 | 2,798 | 285 | 2.05 | 6.95 | +28.6 [27.1, 30.1] | 0.0 |
| Oracle gate + RAG (reads gold) | 69.6 | 55.6 | 84.4 | 51.9 | 29.6 | 14.8 | 39.6 | 1.9 | 4.8 | 13.0 | 24.1 | 13.3 | 91.1 | 38.0 | 749 | 0 | 0.0 | 0.0 | +2.8 [1.1, 4.4] | 0.0005 |

K=30, eval pool (90 scenarios, 3510 questions, 270 per category), dataset v0.3. Answers by Qwen3-8B (thinking off), judged by gpt-6-luna. Every retrieval method uses Qwen3-Embedding-8B; ShopMem and RAG-hybrid + rerank use Qwen3-Reranker-4B. All runs use the reasoning answer format except "Full context", which uses the plain format (its better one). Bold: best non-oracle value. Δ: ShopMem minus the row, paired bootstrap within categories (10,000 resamples); p one-sided.
