# rollmetrics-scoreboard-v1 Leaderboard

> Auto-generated from `knowledge_base/evals/scoreboard_v1/{latest_results.json,splits.json,labels.jsonl}`.
> Refresh after any new evaluation run with `python3 scripts/export_benchmark_leaderboard.py`.

## Run Metadata

- **Benchmark ID**: `rollmetrics-scoreboard-v1`
- **Evaluated At (UTC)**: `2026-11-27T15:13:33+00:00`
- **Reported Split**: `heldout` (`81` bouts in split)
- **Corpus Size**: `122` labeled bouts (`41` calibration / `81` held-out)
- **Splits SHA-256**: `07c4febe68caa6af5a42d41b6c6450be99f7f2844041782c7dcbd67504f34036`
- **Leakage Control**: `strict_blackout_drawbox_plus_pixel_edge_ocr_gate` (cross-split shared `video_id` count: `0`)

## Model Leaderboard

| Rank | Model | Split | N | Winner Acc | Exact Score | MAE Pts | MAE Adv | MAE Pen | Event F1 (@5s) | P50 Latency | Cost / Match | Leakage Gate |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | `gemini-4-hydrogen-agentic` | heldout | 10 | 100.0% | 10.0% | 0.90 | 0.60 | 0.20 | 22.2% | 37.5s | $0.00769 | 10/10 |
| 2 | `gemini-4-neon-agentic` | heldout | 10 | 90.0% | 30.0% | 1.20 | 0.45 | 0.10 | 26.4% | 283.8s | $0.20783 | 10/10 |
| 3 | `ollama/gemma4:e2b-it-q4_K_M-agentic` | heldout | 9 | 55.6% | 0.0% | 1.94 | 0.89 | 0.11 | 4.3% | 234.2s | $0.0000 | 9/9 |
| 4 | `gemma-4-26b-a4b-it` | heldout | 20 | 55.0% | 0.0% | 10.32 | 0.78 | 0.53 | 0.0% | 21.7s | $0.00048 | 20/20 |
| 5 | `gemini-4-hydrogen` | heldout | 20 | 50.0% | 10.0% | 1.60 | 0.72 | 0.53 | 3.7% | 8.1s | $0.00261 | 20/20 |
| 6 | `ollama/gemma4:e2b-it-q4_K_M` | heldout | 81 | 2.5% | 4.9% | 2.40 | 1.12 | 0.57 | 0.0% | 6.3s | $0.0000 | 81/81 |

## Corpus & Split Summary

| Metric | Value |
| --- | ---: |
| Labeled Bouts (`labels.jsonl`) | 122 |
| Calibration Bouts / Videos | 41 / 24 |
| Held-Out Bouts / Videos | 81 / 51 |
| Cross-Split Video Overlap | 0 |
| Human Frame-Audited Bouts | 9 |
| Total Ground-Truth Score Events | 540 |

### Finish Method Distribution

| Finish Method | Bouts |
| --- | ---: |
| `advantages` | 14 |
| `dq` | 1 |
| `points` | 48 |
| `referee_decision` | 5 |
| `submission` | 54 |

## Ground-Truth Labeler Spot-Check Audit

| Metric | Value |
| --- | ---: |
| Spot-Checked Matches | 8 |
| Labeler Winner Accuracy | 75.0% |
| Labeler Final Score Exact Match | 75.0% |
| Labeler MAE Points | 0.625 |
| Labeler MAE Advantages | 0.0625 |
| Labeler MAE Penalties | 0.125 |
| Labeler Score Event F1 | 70.6% |

## Reproduce

```bash
python3 scripts/export_benchmark_leaderboard.py
```
