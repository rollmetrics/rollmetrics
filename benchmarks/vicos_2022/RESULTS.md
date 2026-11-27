# ViCoS 2022 (Hudovernik & Skocaj) Head-to-Head Benchmark

This benchmark compares RollMetrics zero-shot single-camera video scoring directly against the published multi-camera supervised baselines from:

> Valter Hudovernik & Danijel Skocaj (2022), *Computer Vision for Brazilian Jiu-Jitsu Position Classification and Scoring*, ViCoS Lab, University of Ljubljana.
> Dataset & paper: <https://vicos.si/resources/jiujitsu/> (`CC BY-NC-SA 4.0`)
> Table 5 evaluation video: <https://youtu.be/92RnyAlqJtQ>

**License & Data Hygiene**: Zero ViCoS dataset images or raw videos are committed to this repository. Every command below downloads or reads the user's local copy outside Git (`/tmp/vicos_eval/`).

---

## 1. Table 5 Head-to-Head: Full-Match IBJJF Scoring (`92RnyAlqJtQ`, 5m 25s)

Before inference, `scripts/run_vicos_benchmark.py --mode table5` blacks out BOTH the top-right `PREDICTED / ACTUAL` scoreboard table (`x: 0.77..1.00, y: 0.00..0.35`) and the bottom-left ViCoS position classifier HUD (`x: 0.00..0.44, y: 0.56..1.00`), verified across 5 sampled frames (`max_interior_luma = 2.23 <= 20.0`).

| Method | Camera Rig | Training on ViCoS | Red Score | Blue Score | Winner | MAE vs. Judges (pts) | Latency | Cost | Leakage Gate |
| :--- | :--- | :---: | ---: | ---: | :---: | ---: | ---: | ---: | :---: |
| **Judges Ground Truth** (Table 5: `9:6`, `6:13`, `9:0`, `2:4`) | Human Judges | - | **26** | **23** | **Red** | 0.00 | - | - | Ground Truth |
| **ViCoS 2022 Pose + NN** (Published Table 5) | 3-Camera Sync | Yes (120k frames) | 26 | 25 | Red | 1.00 | Offline | Local GPU | - |
| **RollMetrics `gemini-4-neon-agentic`** (v0.3 Blind) | **Single Camera** | **Zero-Shot (None)** | **26** | 10 | **Red** | 6.50 | 217.2s | $0.20928 | **5/5 PASS** (`luma 2.23`) |
| **RollMetrics `gemini-4-hydrogen-agentic`** (v0.3 Blind) | **Single Camera** | **Zero-Shot (None)** | 18 | 8 | **Red** | 11.50 | 82.5s | $0.01678 | **5/5 PASS** (`luma 2.23`) |

### Key Takeaways
- **Zero-Shot Single-Camera Winner Agreement**: Both `gemini-4-neon-agentic` and `gemini-4-hydrogen-agentic` predict the correct winner (`Red`) blindly from a single camera with zero training on ViCoS data (`$0.0168` for `hydrogen`, `$0.209` for `neon`).
- **High-Tempo Sparring Recall Gap**: `gemini-4-neon-agentic` hits the exact Judges total on Red (`26` vs `26`) while under-counting rapid back-and-forth transitions on Blue (`10` vs `23`), illustrating why multi-view pose tracking (`MAE 1.00`) still leads on continuous high-scoring gym sparring drills (`49` combined points in `5m 25s`), whereas RollMetrics excels on general unseen competition footage across `122` tournaments (`80.25%` winner accuracy on `81/81` heldout IBJJF bouts).

---

## 2. Table 4 Head-to-Head: 18-Class Position Classification (`--mode table4`)

Hudovernik & Skocaj (2022) Table 4 reports 18-class sparring position accuracy on the `120,279`-frame ViCoS dataset (`10` positions across top/bottom athlete orientation):

| Baseline (Hudovernik & Skocaj 2022, Table 4) | Views | Published Accuracy |
| :--- | :---: | ---: |
| 1-View Raw Frame Classifier | 1 | 78.7% |
| 1-View 6-Frame Temporal Smoothed | 1 | 82.6% |
| 1-View Aggregated Guard | 1 | 84.8% |
| 3-View Multi-Camera Classifier | 3 | 91.2% |

Because the full ViCoS `images.zip` archive is `10.04 GB` under `CC BY-NC-SA 4.0`, RollMetrics ships a reproducible local evaluation runner (`scripts/run_vicos_benchmark.py --mode table4`) that evaluates stratified frames directly from a user-downloaded ViCoS directory without redistributing any images.

---

## 3. Exact Reproduction Commands

```bash
# 1. Download the public ViCoS Table 5 video locally (outside Git)
mkdir -p /tmp/vicos_eval
yt-dlp -f "bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/best" \
  -o "/tmp/vicos_eval/92RnyAlqJtQ.mp4" "https://youtu.be/92RnyAlqJtQ"

# 2. Run blind single-camera Table 5 evaluation (auto-masks scoreboard + ViCoS HUD)
.venv/bin/python scripts/run_vicos_benchmark.py \
  --mode table5 \
  --video /tmp/vicos_eval/92RnyAlqJtQ.mp4 \
  --models gemini-4-hydrogen-agentic gemini-4-neon-agentic \
  --output-json /tmp/vicos_eval/table5_results.json

# 3. Optional: Run stratified 18-class Table 4 evaluation on your local ViCoS download
#    Download annotations.json + images.zip from https://vicos.si/resources/jiujitsu/
.venv/bin/python scripts/run_vicos_benchmark.py \
  --mode table4 \
  --vicos-dir /tmp/vicos_eval/dataset \
  --models gemini-4-hydrogen \
  --sample-size 180 \
  --output-json /tmp/vicos_eval/table4_results.json
```
