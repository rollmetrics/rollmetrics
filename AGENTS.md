# AGENTS.md

Operational guide for AI coding agents and engineers working inside `rollmetrics`.

## 1. Repository Layout

```text
rollmetrics/
  backend/
    pyproject.toml            # Python 3.12+, FastAPI, google-genai, pytest, pytest-cov, ruff
    rollmetrics/
      config.py               # Settings (ROLLMETRICS_* env prefix, local-first + Cloud Run parity)
      auth.py                 # Google OAuth2 ID token verification, allowlist / open signup, users.json
      main.py                 # FastAPI app, REST endpoints, /ws/live WebSocket, 4 MiB GCS Range proxy, SPA mount
      cli.py                  # `rollmetrics` CLI (serve, analyze, index, eval, media)
      rules/                  # Deterministic IBJJF rulebook v6.1 state machine & legality matrix
      knowledge/              # Hybrid SQLite FTS5 + 768-d vector index, multi-academy tenant isolation
      providers/              # Cloud Gemini 4 (Hydrogen/Neon/Gemma 4 26B) + local Ollama Gemma 4
      video/                  # MatchAnalyzer, LiveRefereeSession, MediaStudio (0.4x slow-mo, posters)
      evals/                  # 3-suite KB evals, model arena, scoreboard_v1 blind-video benchmark
    tests/                    # Pytest suite (rules, knowledge, providers, video, API, CLI, auth)
  frontend/
    package.json              # React 18, TypeScript 5.6, Vite 6, Vitest + @vitest/coverage-v8
    src/
      App.tsx                 # Single-screen film player, Overview Home, /admin, /privacy, /terms
      api.ts                  # Typed fetch client, Bearer/query token auth helpers, snapshot mapper
      types.ts                # Shared TypeScript interfaces
      styles.css              # Matte dark-mat arena CSS (100dvh desktop + 390px mobile zero-overflow)
      __tests__/              # Vitest unit & component tests
    e2e/ux_audit.mjs          # Headless Chromium Playwright UX audit (1440x820 desktop + 390x844 mobile)
  knowledge_base/
    versions/v0.1.0/          # Versioned hybrid SQLite index + SHA256 manifest.json
    evals/                    # IBJJF rule cases, match timelines, coaching queries, scoreboard_v1
  brand/                      # 03 / Kamon Lock SVG marks & 5-belt rank visuals
  DESIGN_LANGUAGE.md          # Visual design tokens and layout discipline
  Dockerfile                  # Multi-stage Node 20 + Python 3.12 Cloud Run image
  .github/workflows/          # CI (ruff, pytest, vitest, build) & Cloud Run deploy
```

## 2. Architecture & Core Invariants

1. **Deterministic Rules + Multimodal Vision**:
   - Vision models emit raw observed grappling events; `backend/rollmetrics/rules/engine.py` deterministically computes IBJJF points, 3.0-second stabilization windows, advantage downgrades, 20.0-second stalling clocks, and the 4-step penalty cascade (1 Warning, 2 +1 Adv to opp, 3 +2 Pts to opp, 4 DQ).
2. **Multi-Academy Tenant Isolation**:
   - Global layer (`academy_id = "global"`): Official IBJJF Rulebook v6.1 + official IBJJF YouTube rules videos, shared across all users.
   - Private academy layer (`academy_id` per school in SQLite `documents` + `data/runtime/academies.json`): each school manages its own private coaching notes and reference video links via `/api/academies/me` and `/api/academies/me/references`.
   - Kory's seeded default academy is `cta` (`Caio Terra Association`).
   - Strict invariant: queries for Academy B must NEVER return Academy A's private reference documents.
3. **Zero-Leakage Blind-Video Evaluation (`rollmetrics-scoreboard-v1`)**:
   - All scoreboard benchmark frames MUST mask out broadcast scoreboards/HUD overlays before vision inference (`100% zero-pixel` leakage gate).
   - Never tune prompts, thresholds, or weights on the `heldout` split; iterate strictly on `calibration`.
4. **Cloud Run Video Streaming & iOS Safari Compatibility**:
   - `GET` and `HEAD` `/api/matches/{match_id}/video` MUST return bounded `206 Partial Content` responses capped at `MAX_RANGE_CHUNK_BYTES = 4 MiB` (Cloud Run HTTP/1.1 enforces a hard 32 MiB response limit).
   - Unbounded `bytes=0-` requests are clamped to `bytes=0-4194303` with accurate `Content-Range` and `Accept-Ranges: bytes`.
   - In Cloud Run, range requests proxy directly from GCS via async `httpx` streaming (`_proxy_gcs_video_range`) without downloading the entire MP4 into container RAM.
   - Never delete `data/runtime`.
5. **Authentication & Public Legal Routes**:
   - When `ROLLMETRICS_ENVIRONMENT=cloud`, all `/api/*` and `/ws/*` routes except `/healthz` and `/api/auth/config` enforce verified Google OAuth2 ID tokens (`google.oauth2.id_token.verify_oauth2_token`).
   - `ROLLMETRICS_ALLOW_OPEN_SIGNUP=false` (default) enforces `ROLLMETRICS_ALLOWLIST_EMAILS`. Setting `true` permits any verified Google account with isolated per-user storage in `data/runtime/users.json`.
   - `/privacy` and `/terms` MUST remain publicly reachable without authentication for Google OAuth verification.

## 3. Design Language & Copy Rules

- **Brand Mark**: `03 / Kamon Lock` (`brand/logo-mark.svg`, `frontend/public/logo-mark.svg`). Pure monochrome `#F8FAFC` on matte tatami `#0B0E13`. Always render the wordmark as `RollMetrics` (never `rollmetrics.ai`).
- **5-Belt Palette**: White `#F8FAFC`, Blue `#2563EB`, Purple `#7C3AED`, Brown `#854D0E`, Black `#11151C` (with red rank bar `#DC2626`).
- **Spotless Main Mat Stage**: Desktop (`1440x820`) fits in `100dvh` with zero scroll. Mobile (`390x844`) scrolls cleanly with zero horizontal overflow (`scrollWidth <= innerWidth`). Never place index versions (`v0.1.0`), SHA hashes, codec labels, or admin links on the main Film Review screen; keep them inside Overview Home, the Connection & Model Config modal, and `/admin`.
- **Strict Copy Rule**: NEVER use em-dashes (Unicode `U+2014`) or `"not X, it's Y"` contrasts anywhere in UI copy, docs, skills, comments, or commit messages.

## 4. Exact Local Dev, Test, Coverage & Build Commands

```bash
# 1. Backend lint & tests with coverage (target >= 85%)
env PATH="/usr/bin:/bin" .venv/bin/ruff check backend
env PATH="/usr/bin:/bin" .venv/bin/pytest --cov=rollmetrics --cov-report=term-missing

# 2. Frontend unit tests, coverage (target >= 80-85%), and production build
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
cd frontend
npm test
npm run test:coverage
npm run build
cd ..

# 3. Start local FastAPI + SPA server on port 8090
.venv/bin/uvicorn rollmetrics.main:app --host 0.0.0.0 --port 8090

# 4. Run Playwright Desktop (1440x820) + Mobile (390x844) UX audit
cd frontend && npm run test:ux
```

## 5. Cloud Run Deployment Invariants

- Production URL: `https://rollmetrics.ca` (Google Cloud Run service `rollmetrics` in `us-central1`).
- Deploy workflow (`.github/workflows/deploy.yml`) builds the multi-stage `Dockerfile` (which compiles `frontend/dist` and bundles `backend/` + `knowledge_base/versions/v0.1.0/`) and stamps `ROLLMETRICS_BUILD_SHA` from the git short SHA.
- Always verify `/healthz` (`build_sha` matches pushed commit), `/api/auth/config`, `/privacy`, `/terms`, and mobile/desktop video Range streaming after every deploy.
