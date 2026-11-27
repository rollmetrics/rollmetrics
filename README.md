<p align="center">
  <img src="frontend/public/logo-mark.svg" alt="RollMetrics Kamon Lock mark" width="64" height="64" />
</p>

<h1 align="center">RollMetrics</h1>

<p align="center">
  <strong>Open-source Jiu-Jitsu match scoring, film study, and private academy coaching.</strong>
</p>

<p align="center">
  <a href="https://rollmetrics.ca">Live Beta</a> ·
  <a href="benchmarks/scoreboard_v1/LEADERBOARD.md">Benchmark Leaderboard</a> ·
  <a href="CONTRIBUTING.md">Contributing</a> ·
  <a href="LICENSE">MIT License</a>
</p>

---

RollMetrics turns competition and sparring video into synced **Match Moments**, deterministic **IBJJF v6.1** scoring with clickable rule citations, looping **0.4x slow-motion GIFs**, and embedded pointers to your gym's own private curriculum. Run it 100% offline on hardware you own with **Ollama Gemma 4**, or connect **Google Gemini 4** in the cloud.

## Quickstart (Local & Offline)

**Requirements:** Python 3.12+, Node 20+, `ffmpeg`, and [Ollama](https://ollama.com) (for offline mode) or a Gemini API key (for cloud mode).

```bash
git clone https://github.com/rollmetrics/rollmetrics.git
cd rollmetrics
cp .env.example .env

# Backend + Frontend
python3 -m venv .venv && .venv/bin/pip install -e "backend[dev]"
npm --prefix frontend install && npm --prefix frontend run build

# Pull local offline vision model (optional if using Cloud Gemini)
ollama pull gemma4:e2b-it-q4_K_M

# Launch locally at http://127.0.0.1:8090
./scripts/run_local.sh
```

## What It Does

- **Deterministic IBJJF v6.1 Rules Engine:** Tracks `3.0s` position stabilization, `20.0s` stalling cascades, a `300`-case belt/age legality matrix, and clickable official rule excerpts + videos. (Switchable **ADCC** ruleset tracked in [Issue #1](https://github.com/rollmetrics/rollmetrics/issues/1).)
- **Match Moments & Inline GIFs:** Jump between key exchanges, replay looping `0.4x` slow-motion clips inline, and share read-only breakdowns via `/share/{token}`.
- **Private Academy Curricula:** Each gym can attach its own private coaching notes and hosted reference videos alongside the shared IBJJF rulebook and open-source syllabus.
- **Local-First or Cloud:** Run completely offline on local gym video folders (`rollmetrics desktop`) or deploy to Cloud Run.

## Blind-Video Benchmark (`rollmetrics-scoreboard-v1`)

Evaluated on `122` real tournament bouts (`41` calibration / `81` heldout, `0` cross-split video overlap) with `100%` scoreboard blackout masking and audio stripped (`-an`). Full methodology and results in [benchmarks/scoreboard_v1/LEADERBOARD.md](benchmarks/scoreboard_v1/LEADERBOARD.md).

| Model & Harness (`heldout`, 100% Blind) | Winner Accuracy | Points MAE | A/B Swaps | Cost / Match |
| --- | ---: | ---: | ---: | ---: |
| `gemini-4-hydrogen-agentic` (`480p` mat crop + anchor + retry) | **100.0%** | **0.90** | **0.0%** | **$0.0077** |
| `gemini-4-neon-agentic` (`480p` mat crop + anchor) | **90.0%** | 1.20 | 10.0% | $0.2078 |
| `ollama/gemma4:e2b` (3-stage offline local agentic) | **55.6%** | 1.94 | 22.2% | **$0.0000** |

## Tests & Coverage

```bash
# Backend (pytest + coverage)
.venv/bin/ruff check backend
.venv/bin/pytest --cov=rollmetrics --cov-fail-under=92

# Frontend (Vitest + coverage)
npm --prefix frontend run test:coverage
```

## License

MIT License. See [LICENSE](LICENSE) for details.
