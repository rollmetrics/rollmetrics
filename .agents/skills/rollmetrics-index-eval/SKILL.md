---
name: rollmetrics-index-eval
description: >-
  Build, extend, import private academy notes into, and benchmark the versioned RollMetrics IBJJF + CTA knowledge base index. Activate this skill when rebuilding knowledge_base versions, importing new curriculum transcripts, verifying SHA256 manifests, or running the 3-part RollMetrics evaluation suite.
---

# RollMetrics Knowledge Index & Benchmark Suite

Use this workflow to build or update versioned IBJJF + CTA hybrid SQLite indexes (`FTS5 BM25 + 768-d vectors`), ingest private curriculum drops, verify artifact SHA256 hashes, and gate changes with the 3-part RollMetrics benchmark suite.

## Procedure

1. **Build or Rebuild a Versioned Knowledge Index**
   - Run the indexer from `/Users/korymath/Documents/code/rollmetrics`:
     ```bash
     cd /Users/korymath/Documents/code/rollmetrics
     .venv/bin/rollmetrics index build --version v0.1.0
     ```
   - Add `--embed-cloud` when `GEMINI_API_KEY` is configured to compute `gemini-embedding-2` (`output_dimensionality=768`) embeddings; omit `--embed-cloud` for deterministic offline 768-d n-gram vectors.
   - Verify `knowledge_base/versions/<version>/manifest.json` contains non-zero `ibjjf` and `cta` document counts and matching SHA256 checksums for `ibjjf_rules.jsonl`, `cta_curriculum.jsonl`, and `index.sqlite`.

2. **Import Private Academy or Curriculum Notes**
   - Drop local Markdown, JSON, or transcript files into a source directory and create a new immutable index version without mutating `v0.1.0`:
     ```bash
     cd /Users/korymath/Documents/code/rollmetrics
     .venv/bin/rollmetrics index import-private /path/to/notes --target-version v0.2.0
     ```
   - Update `ROLLMETRICS_ACTIVE_INDEX_VERSION=v0.2.0` only after benchmarks pass on the new version.

3. **Run the 3-Part Evaluation Suite**
   - Execute the full benchmark runner via CLI or `POST /api/evals/run`:
     ```bash
     cd /Users/korymath/Documents/code/rollmetrics
     .venv/bin/rollmetrics eval --version v0.1.0
     ```
   - Confirm all three suites pass (`overall_pass: true`):
     1. **IBJJF Rule & Legality Cases (`ibjjf_rule_cases.json`)**: Belt/uniform/age foul verdicts, 3.0s stabilization vs advantage downgrades, penalty cascade math, and rule article retrieval.
     2. **Match Timeline Accuracy (`match_timelines.json`)**: Exact score, advantage, penalty, winner, and win-method reproduction across audited tournament matches (Mastermind Jiu Jitsu Cup 9 Semifinal & Final) and regulatory edge cases.
     3. **CTA Coaching Retrieval (`cta_coaching_queries.json`)**: Positional coaching `Recall@3` and valid YouTube timestamp jump-links.

4. **Verify Lint & Automated Tests**
   - Run static checks and unit tests before committing any index or schema update:
     ```bash
     cd /Users/korymath/Documents/code/rollmetrics
     .venv/bin/ruff check backend
     .venv/bin/pytest -v
     ```
