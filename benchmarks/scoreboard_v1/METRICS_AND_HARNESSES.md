# RollMetrics Benchmark Metrics & Evaluation Harnesses

This document defines every metric, formula, worked example, and model harness reported on the [RollMetrics Scoreboard v1 Leaderboard](LEADERBOARD.md) and the [122-Bout Dataset Catalog](DATASET_CATALOG.md).

## 1. Benchmark Split & Leakage Guarantees

- **Dataset**: `rollmetrics-scoreboard-v1` (`122` public competition bouts across `2024`-`2026` IBJJF Worlds, Pans, Euros, Brasileiro, The Crown, No-Gi Worlds/Pans/Euros, and Mastermind Cup 9).
- **Zero-Leakage Split**: `calibration` (`41` bouts) and `heldout` (`81` bouts) share **`0` YouTube `video_id`s** (`splits.json`, enforced in CI). Prompts and code never reference heldout match IDs.
- **Pixel-Verified Scoreboard Masking**: Before any video or frame reaches a VLM, every broadcast scoreboard/clock/banner region (`flograppling_lower_bar`, `smoothcomp_lower_left`, `ibjjf_upper_bar`) is blacked out with `ffmpeg drawbox` and verified by `verify_video_masked(...)` (`max_luma <= 20.0` at H.264 480p, `0.0` on lossless PNG frames).
- **Ground Truth Sanity**: Ground-truth labels (`labels.jsonl`) are extracted exclusively from the masked-out scoreboard crop via Vision OCR + temporal stabilization, with human spot-checks and an IBJJF CI sanity gate (`points != 1`).

---

## 2. Metric Definitions, Formulas & Worked Examples

### Winner Accuracy (`winner_accuracy`)
- **Plain English**: Fraction of evaluated bouts where the model's predicted winner (`"A"`, `"B"`, or `"draw"`) matches the official ground-truth winner.
- **Formula**:
  $$\text{Winner Accuracy} = \frac{1}{N}\sum_{i=1}^{N} \mathbb{I}(\hat{w}_i = w_i)$$
- **Worked Example**: On `10` heldout bouts, if the model predicts the exact winner on `9` bouts and misses `1`, `Winner Accuracy = 9 / 10 = 90.0%`.

### Final Score Exact Match (`final_score_exact_match`)
- **Plain English**: Fraction of bouts where predicted `(points_A, points_B)` matches the official final point totals for BOTH athletes simultaneously.
- **Formula**:
  $$\text{Final Score Exact Match} = \frac{1}{N}\sum_{i=1}^{N} \mathbb{I}(\hat{p}_{i,A} = p_{i,A} \land \hat{p}_{i,B} = p_{i,B})$$
- **Worked Example**: Ground truth is `6-2`. Prediction `6-2` counts as `1`; prediction `6-0` counts as `0`.

### Points MAE (`points_mae`), Advantages MAE (`advantages_mae`), Penalties MAE (`penalties_mae`)
- **Plain English**: Mean absolute error per athlete score channel across all evaluated bouts.
- **Formula**:
  $$\text{Points MAE} = \frac{1}{2N}\sum_{i=1}^{N}\left(|\hat{p}_{i,A} - p_{i,A}| + |\hat{p}_{i,B} - p_{i,B}|\right)$$
  (Identical form for Advantages MAE and Penalties MAE.)
- **Worked Example**: Ground truth `4-0`, prediction `6-0`. Errors are `|6-4|=2` and `|0-0|=0`, so `Points MAE = (2 + 0) / 2 = 1.00`.

### Score Event F1 (`+/-5s`) (`event_f1_5s`)
- **Plain English**: Harmonic mean of precision and recall for individual timestamped scoring events, using greedy 1-to-1 matching where a predicted event matches a ground-truth event if the athlete (`A`/`B`), point delta (`pts_delta`), and timestamp (`|t_pred - t_gt| <= 5.0s`) all align.
- **Formula**:
  $$\text{Precision} = \frac{\text{TP}}{\text{TP} + \text{FP}}, \quad \text{Recall} = \frac{\text{TP}}{\text{TP} + \text{FN}}, \quad \text{F1} = \frac{2 \cdot \text{Precision} \cdot \text{Recall}}{\text{Precision} + \text{Recall}}$$
- **Worked Example**: Ground truth has `3` scoring events; model predicts `4` events, of which `2` match within `+/-5.0s` with matching athlete and point value. `Precision = 2/4 = 0.50`, `Recall = 2/3 = 0.667`, `F1 = 0.571`.

### A/B Swap Rate (`ab_swap_rate`)
- **Plain English**: Fraction of bouts with a decisive winner (`A` or `B`) where the model predicted the opposite athlete (`A <-> B` identity inversion).
- **Formula**:
  $$\text{A/B Swap Rate} = \frac{1}{N}\sum_{i=1}^{N} \mathbb{I}(\hat{w}_i \in \{A, B\} \land w_i \in \{A, B\} \land \hat{w}_i \neq w_i)$$

### Tap-to-Identify ID-Lock % (`id_lock_pct`)
- **Plain English**: Percentage of bouts where a user's `(x_norm, y_norm)` tap anchor at `t=2s` remains locked to the exact same athlete at `t=0`, `t=mid`, and `t=end` (`IoU >= 0.25` and no `A <-> B` label flip), reported overall plus on `matching_kit` (same-color gi / black-rashguard) and `No-Gi` slices (`backend/rollmetrics/evals/id_lock_eval.py`).
- **Formula**:
  $$\text{ID-Lock \%} = \frac{100}{N}\sum_{i=1}^{N} \mathbb{I}(\text{lock}_{t_0}(i) \land \text{lock}_{t_{\text{mid}}}(i) \land \text{lock}_{t_{\text{end}}}(i))$$

### Latency (`p50_latency_ms`, `p95_latency_ms`) & Cost/Match (`mean_cost_usd`)
- **Plain English**: End-to-end wall-clock latency (median P50 and 95th percentile P95 in milliseconds, including upload/inference/rule-engine replay) and mean API token cost in USD per match (`$0.00` for local Ollama runs).

---

## 3. Versioned Harness Specifications

| Harness Version | Target Models | Video Resolution & Sampling | Mat Crop / Identity Anchor | Prompt & Rule Engine |
| :--- | :--- | :--- | :--- | :--- |
| **`v0.1` Storyboard** | `gemma-4-26b-a4b-it`, `gemini-4-hydrogen`, `ollama/gemma4:e2b-it-q4_K_M` | `24` uniformly sampled masked JPEG frames (`<= 768px`) in a single multi-image call | Full broadcast frame (scoreboard masked) | Single-pass JSON event extraction + `IBJJFRefereeEngine` replay (`backend/rollmetrics/evals/scoreboard_bench.py`) |
| **`v0.2` 360p Agentic** | `gemini-4-hydrogen-agentic`, `gemini-4-neon-agentic` | Full masked MP4 at `360p` via Gemini Files API (`1 fps` native video tokens) | Full broadcast frame (scoreboard masked) | 2-pass coarse scan + targeted 10s HD zoom-in verification (`score_masked_video_agentic`) + `IBJJFRefereeEngine` |
| **`v0.3` 480p Dynamic Mat Crop + Visual Anchor** | `gemini-4-hydrogen-agentic`, `gemini-4-neon-agentic` | Full masked MP4 at **`480p`** via Gemini Files API | **`compute_mat_crop_box(...)`** zooms onto the active mat (`backend/rollmetrics/video/tracking.py`) + **`t=+2s` annotated Visual A/B Identity Anchor** (`A` cyan / `B` amber) + **Zero-Event Retry** + **Temporal Action Smoother** | 2-pass agentic zoom-in + deterministic `IBJJFRefereeEngine` (`3.0s` stabilization, `20.0s` stalling, penalty cascade) |
| **`v0.3` Local 3-Stage Ollama** | `ollama/gemma4:e2b-it-q4_K_M-agentic` | 3-stage local sliding window (`4-6` masked frames per stage, `240s` timeout) | Scoreboard-masked mat frames + `t=+2s` athlete appearance cue | Local `OllamaLocalProvider` (`backend/rollmetrics/providers/ollama_local.py`) + `IBJJFRefereeEngine` |

---

## 4. Exact Reproduction Commands

All commands run from the repository root after `.venv/bin/pip install -e ".[dev]"` (or `uv sync --all-extras`).

```bash
# 1. Replay the deterministic benchmark metrics from cached predictions (offline, zero API cost)
.venv/bin/python -c '
from rollmetrics.evals.scoreboard_bench import run_scoreboard_bench
for split in ("calibration", "heldout"):
    res = run_scoreboard_bench(split=split, live_video=False, persist=True)
    print(split, res["models"].keys())
'

# 2. Regenerate LEADERBOARD.md and DATASET_CATALOG.md from latest_results.json + matches/labels
.venv/bin/python scripts/export_benchmark_leaderboard.py

# 3. Run the Athlete Tap-to-Identify ID-Lock % eval suite
.venv/bin/python -c '
from rollmetrics.evals.id_lock_eval import evaluate_athlete_id_lock
print(evaluate_athlete_id_lock().model_dump_json(indent=2))
'

# 4. Run the full 3-part Knowledge Base & Referee Rules benchmark suite
.venv/bin/rollmetrics eval --version v0.1.0

# 5. Run live blind-video scoring on a split (requires GEMINI_API_KEY in .env and local eval_media/)
.venv/bin/python -c '
from rollmetrics.evals.scoreboard_bench import run_scoreboard_bench
run_scoreboard_bench(split="heldout", models=["gemini-4-hydrogen-agentic"], live_video=True, persist=True)
'
```
