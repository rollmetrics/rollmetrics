# Contributing to RollMetrics

Thank you for contributing to RollMetrics. Please follow these guidelines to keep the codebase clean, reproducible, and safe for open-source distribution.

## Development Setup

```bash
uv venv .venv --python 3.12
uv pip install --python .venv/bin/python -e ".[dev]"
.venv/bin/rollmetrics index build --version v0.1.0

cd frontend
npm ci
npm run build
cd ..
```

## Required Quality Checks

Run all checks locally before opening a pull request:

```bash
# Backend lint and coverage gate
.venv/bin/ruff check backend
.venv/bin/pytest -v --cov=rollmetrics --cov-report=term-missing --cov-fail-under=92

# Knowledge base benchmark
.venv/bin/rollmetrics eval --version v0.1.0

# Frontend tests, coverage, and build
cd frontend
npm run test:coverage
npm run build
cd ..
```

## Data, Privacy, and Licensing Rules

1. Never commit `.env` files, API keys, OAuth client IDs, GCP project numbers, billing account IDs, or personal email addresses. Use placeholder values (`coach@example.com`, `your-gcp-project`) in tests and documentation.
2. Never commit raw video files (`.mp4`, `.mov`, `.mkv`) or runtime user data (`data/runtime/`).
3. Respect third-party dataset licenses. Research-only adapters such as the ViCoS Brazilian Jiu-Jitsu Positions Dataset (`CC BY-NC-SA 4.0`) must read external user-supplied directories only and must never bundle dataset images or commercial weights in this repository.
4. Preserve zero-leakage scoreboard masking and split integrity (`0` shared `video_id` across `calibration` and `heldout`) in `knowledge_base/evals/scoreboard_v1/`.
