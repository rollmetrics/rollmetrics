# `rollmetrics-scoreboard-v1`: Open BJJ AI Referee & Scoreboard Benchmark

`rollmetrics-scoreboard-v1` is an open 2026 evaluation benchmark for full-match Brazilian Jiu-Jitsu (BJJ) IBJJF adjudication from real single-camera tournament and smartphone footage.

See [LEADERBOARD.md](./LEADERBOARD.md) for current model rankings, latency, cost, and split statistics.

## Benchmark Protocol

1. **Full-Match IBJJF Ground Truth**: Every bout in `knowledge_base/evals/scoreboard_v1/labels.jsonl` records active bout start/end timestamps, athlete uniform descriptions, timestamped scoring events (points, advantages, penalties), final scoreboards, official winner, and finish method (`points`, `submission`, `dq`, or `referee_decision`).
2. **Zero-Leakage Scoreboard Masking**: Broadcast overlays and mat-side digital scoreboards are masked before any model sees a frame using `ffmpeg` `drawbox` blackout plus an automated pixel/edge OCR verification gate (`strict_blackout_drawbox_plus_pixel_edge_ocr_gate`). Models must infer IBJJF points, advantages, penalties, and stoppages strictly from mat action.
3. **Video-ID-Grouped Deterministic Splits**: `knowledge_base/evals/scoreboard_v1/splits.json` locks a 30/70 calibration vs. held-out split grouped by source `video_id` and verified by `splits_content_sha256`, guaranteeing `0` shared source videos across splits.
4. **Human Spot-Check Audit**: Ground-truth labeler reliability is tracked explicitly via frame-audited matches (`spot_check_audit`) alongside automated scoreboard OCR and early-stoppage hand-raise audits.

## Comparison vs. ViCoS BJJ Positions Dataset (2022)

| Dimension | `rollmetrics-scoreboard-v1` (2026) | ViCoS BJJ Positions Dataset (Hudovernik & Skocaj, MMSports '22) |
| --- | --- | --- |
| **Camera Setup** | Single-camera real broadcast & smartphone tournament footage | 3-camera fixed laboratory rig |
| **Evaluation Task** | Full-match IBJJF refereeing: scoring timeline, final points/adv/pen, winner, stoppage method | Single-frame 18-class grappling position classification + COCO keypoints |
| **Temporal Scope** | Multi-minute full bouts with 3.0s stabilization and early-stoppage handling | Frame-level position segments (~120k frames) |
| **Leakage Controls** | Strict scoreboard blackout (`drawbox`) + pixel/edge OCR gate + video-ID split lock | Controlled lab recording without broadcast scoreboards |
| **Domain Diversity** | Gi & No-Gi tournaments across multiple divisions, venues, lighting, and camera angles | Controlled academy/lab mat setting |
| **License & Commercial Use** | Open RollMetrics benchmark protocol & evaluation harness (no third-party video redistribution) | **`CC BY-NC-SA 4.0`** (NonCommercial + ShareAlike) |

## Verified ViCoS `CC BY-NC-SA 4.0` Non-Commercial License Notice

The ViCoS *Brazilian Jiu-Jitsu Positions Dataset* (`vicos.si`, Hudovernik & Skocaj, ACM MMSports '22) is distributed under **`CC BY-NC-SA 4.0` (Attribution-NonCommercial-ShareAlike 4.0 International)**.

To strictly respect that license:

- RollMetrics **never bundles** ViCoS images, annotations, or derived media in this repository.
- RollMetrics **never trains** commercial weights or production models on ViCoS data.
- Any ViCoS position evaluation in RollMetrics runs exclusively through an optional, research-only local-directory adapter against a user-supplied local download.

## Reported Metrics

- **Winner Accuracy**: Exact match against official bout winner (`A`, `B`, or `referee_decision`).
- **Final Score Exact Match**: Exact match on final `(points, advantages, penalties)` for both athletes.
- **MAE (Points, Advantages, Penalties)**: Mean absolute error per athlete on final scoreboard totals.
- **Score Event F1 (@5.0s)**: Precision, recall, and F1 of timestamped scoring events matched within `+/-5.0s` tolerance.
- **Latency & Cost**: P50/P95 per-match latency and mean USD cost per match (local Ollama Gemma models evaluate at `$0.0000` marginal API cost).
- **Leakage Gate**: Count of evaluated bouts that passed pixel/edge blackout verification.

## Reproducible CLI Commands

### 1. Export / Refresh `LEADERBOARD.md`

```bash
python3 scripts/export_benchmark_leaderboard.py
```

Custom input or output paths:

```bash
python3 scripts/export_benchmark_leaderboard.py \
  --scoreboard-dir knowledge_base/evals/scoreboard_v1 \
  --output benchmarks/scoreboard_v1/LEADERBOARD.md \
  --stdout
```

### 2. Run RollMetrics Evaluation Suites

```bash
# Run core IBJJF fouls, match timeline, and CTA retrieval benchmarks
.venv/bin/rollmetrics eval --version v0.1.0

# Compare cloud Gemini 4 and local Ollama Gemma 4 models
.venv/bin/rollmetrics eval --version v0.1.0 --compare-models

# Run scoreboard benchmark runner against held-out split
.venv/bin/python -m rollmetrics.evals.scoreboard_bench.runner --split heldout
```

### 3. Verify Benchmark Export & Test Suite

```bash
.venv/bin/pytest backend/tests/test_leaderboard_export.py -v
```
