import { useRef, useState, type RefObject } from "react";

interface LandingPageProps {
  gisButtonRef: RefObject<HTMLDivElement>;
  authError: string | null;
  formatAuthGateError: (raw: string) => string;
  onNavigateLegal: (mode: "privacy" | "terms") => void;
  onDemoLogin?: () => void;
  demoLoginBusy?: boolean;
}

interface LandingMoment {
  id: string;
  videoSec: number;
  leftPct: number;
  tone: "adv" | "win" | "pen";
  label: string;
  tag: string;
  tagClass: string;
  title: string;
  detail: string;
  clock: string;
  scoreA: [number, number, number];
  scoreB: [number, number, number];
}

const GITHUB_URL = "https://github.com/rollmetrics/rollmetrics";
const CLIP_DURATION_SEC = 11.0;
const CLIP_START_CLOCK_SEC = 154; // 02:34

const LANDING_MOMENTS: LandingMoment[] = [
  {
    id: "sweep-top",
    videoSec: 0.5,
    leftPct: 10,
    tone: "adv",
    label: "Sweep reversal to top (02:34)",
    tag: "SWEEP · 02:34",
    tagClass: "adv",
    title: "Sweep reversal to top",
    detail: "Caio Terra comes up on top from open guard",
    clock: "02:34",
    scoreA: [2, 2, 2],
    scoreB: [2, 2, 2],
  },
  {
    id: "stab-hold",
    videoSec: 3.5,
    leftPct: 36,
    tone: "adv",
    label: "Top base stabilization (02:31)",
    tag: "3s HOLD · 02:31",
    tagClass: "adv",
    title: "Top base stabilization",
    detail: "IBJJF Art. 4.1 (3.0s top control verified)",
    clock: "02:31",
    scoreA: [2, 2, 2],
    scoreB: [2, 2, 2],
  },
  {
    id: "lead-4-2",
    videoSec: 6.5,
    leftPct: 64,
    tone: "win",
    label: "Caio Terra takes 4-2 lead (02:28)",
    tag: "+2 PTS · 02:28",
    tagClass: "win",
    title: "Caio Terra takes 4-2 lead",
    detail: "IBJJF Art. 4.6 (2 pts confirmed on board)",
    clock: "02:28",
    scoreA: [4, 2, 2],
    scoreB: [2, 2, 2],
  },
  {
    id: "top-pass",
    videoSec: 9.5,
    leftPct: 88,
    tone: "win",
    label: "Collar-and-pant pass pressure (02:25)",
    tag: "TOP PASS · 02:25",
    tagClass: "win",
    title: "Collar-and-pant pass pressure",
    detail: "Caio Terra controls hips ahead 4-2",
    clock: "02:25",
    scoreA: [4, 2, 2],
    scoreB: [2, 2, 2],
  },
];

function formatClockSec(totalSec: number): string {
  const clamped = Math.max(0, Math.round(totalSec));
  const m = Math.floor(clamped / 60);
  const s = clamped % 60;
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

export function LandingPage({
  gisButtonRef,
  authError,
  formatAuthGateError,
  onNavigateLegal,
  onDemoLogin,
  demoLoginBusy,
}: LandingPageProps) {
  const previewVideoRef = useRef<HTMLVideoElement | null>(null);
  const [activeIdx, setActiveIdx] = useState(2);
  const [progressPct, setProgressPct] = useState(LANDING_MOMENTS[2].leftPct);
  const [liveClock, setLiveClock] = useState(LANDING_MOMENTS[2].clock);

  const activeMoment = LANDING_MOMENTS[activeIdx];

  const selectMoment = (idx: number) => {
    const next = LANDING_MOMENTS[idx];
    setActiveIdx(idx);
    setProgressPct(next.leftPct);
    setLiveClock(next.clock);
    if (previewVideoRef.current) {
      previewVideoRef.current.currentTime = next.videoSec;
    }
  };

  const handleVideoTimeUpdate = () => {
    const v = previewVideoRef.current;
    if (!v) return;
    const t = Math.max(0, Math.min(CLIP_DURATION_SEC, v.currentTime || 0));
    setProgressPct(Math.round((t / CLIP_DURATION_SEC) * 100));
    setLiveClock(formatClockSec(CLIP_START_CLOCK_SEC - t));
    let matchedIdx = 0;
    for (let i = 0; i < LANDING_MOMENTS.length; i += 1) {
      if (t >= LANDING_MOMENTS[i].videoSec) matchedIdx = i;
    }
    setActiveIdx(matchedIdx);
  };

  return (
    <div className="landing-page" data-testid="cloud-signin-gate">
      <header className="landing-header">
        <div className="landing-header-inner">
          <div className="landing-brand">
            <img src="/logo-mark.svg" alt="RollMetrics mark" className="landing-brand-mark" />
            <span className="landing-brand-wordmark">RollMetrics</span>
            <span className="landing-belts" aria-hidden="true">
              <span className="landing-belt white" />
              <span className="landing-belt blue" />
              <span className="landing-belt purple" />
              <span className="landing-belt brown" />
              <span className="landing-belt black" />
            </span>
          </div>
          <nav className="landing-header-nav" aria-label="Primary links">
            <a
              href={GITHUB_URL}
              target="_blank"
              rel="noreferrer"
              className="landing-nav-link"
              data-testid="landing-header-github"
            >
              GitHub
            </a>
          </nav>
        </div>
      </header>

      <main className="landing-main">
        <section className="landing-hero" id="signin">
          <div className="landing-hero-copy">
            <h1 className="landing-h1">Break down jiu-jitsu matches.</h1>
            <p className="landing-lead">
              Automatic scoring, key moments, and study clips from any match video.
            </p>

            <div className="landing-cta-block">
              <div className="landing-cta-row">
                <div
                  ref={gisButtonRef}
                  data-testid="google-gis-slot"
                  className="landing-gis-slot"
                />
                {onDemoLogin && (
                  <a
                    href="#demo"
                    className="landing-secondary-btn"
                    data-testid="demo-sebastian-btn"
                    aria-disabled={demoLoginBusy ? "true" : undefined}
                    onClick={(e) => {
                      e.preventDefault();
                      if (!demoLoginBusy) onDemoLogin();
                    }}
                  >
                    {demoLoginBusy ? "Opening Demo..." : "Try Demo (Sebastian)"}
                  </a>
                )}
                <a
                  href={GITHUB_URL}
                  target="_blank"
                  rel="noreferrer"
                  className="landing-secondary-btn"
                  data-testid="landing-github-cta"
                >
                  GitHub
                </a>
              </div>
              {authError && (
                <div className="auth-gate-error" role="alert">
                  {formatAuthGateError(authError)}
                </div>
              )}
            </div>
          </div>

          <div className="landing-preview-card" aria-label="Sample match analysis output">
            <div className="landing-preview-top">
              <span>Match Moments</span>
              <span>Worlds 2019 · Rooster QF</span>
            </div>

            <div className="landing-video-preview" data-testid="landing-video-preview">
              <video
                ref={previewVideoRef}
                src="/landing-preview.mp4"
                poster="/landing-preview-poster.jpg"
                autoPlay
                loop
                muted
                playsInline
                onTimeUpdate={handleVideoTimeUpdate}
                className="landing-video-thumb"
                aria-label="Caio Terra vs Bruno Malfacine 2019 IBJJF Worlds clip"
              />
            </div>

            <div
              className="landing-scrubber"
              data-testid="landing-timeline"
              role="group"
              aria-label="Interactive match moment markers"
            >
              <div
                className="landing-scrubber-fill"
                style={{ width: `${progressPct}%` }}
              />
              {LANDING_MOMENTS.map((m, idx) => (
                <button
                  key={m.id}
                  type="button"
                  className={`landing-pin ${m.tone}${idx === activeIdx ? " active" : ""}`}
                  style={{ left: `${m.leftPct}%` }}
                  data-testid={`landing-pin-${idx}`}
                  aria-label={m.label}
                  aria-pressed={idx === activeIdx}
                  onClick={() => selectMoment(idx)}
                />
              ))}
            </div>

            <div className="landing-scoreboard" data-testid="landing-scoreboard">
              <div className="landing-score-side">
                <span className="landing-score-name">
                  <span className="landing-gi-dot white" aria-label="White Gi" />
                  Terra
                </span>
                <div className="landing-score-nums" data-testid="landing-score-a">
                  <span className="pts">{activeMoment.scoreA[0]}</span>
                  <span className="adv">{activeMoment.scoreA[1]}</span>
                  <span className="pen">{activeMoment.scoreA[2]}</span>
                </div>
              </div>
              <div className="landing-score-clock" data-testid="landing-score-clock">
                {liveClock}
              </div>
              <div className="landing-score-side right">
                <div className="landing-score-nums" data-testid="landing-score-b">
                  <span className="pts">{activeMoment.scoreB[0]}</span>
                  <span className="adv">{activeMoment.scoreB[1]}</span>
                  <span className="pen">{activeMoment.scoreB[2]}</span>
                </div>
                <span className="landing-score-name">
                  Malfacine
                  <span className="landing-gi-dot blue" aria-label="Blue Gi" />
                </span>
              </div>
            </div>

            <div className="landing-output-list" data-testid="landing-moments-list">
              {LANDING_MOMENTS.map((m, idx) => (
                <button
                  key={m.id}
                  type="button"
                  className={`landing-output-item interactive${idx === activeIdx ? " active" : ""}`}
                  data-testid={`landing-moment-${idx}`}
                  aria-pressed={idx === activeIdx}
                  onClick={() => selectMoment(idx)}
                >
                  <span className={`landing-output-tag ${m.tagClass}`}>
                    {m.tag}
                  </span>
                  <div className="landing-output-text">
                    <strong>{m.title}</strong> · {m.detail}
                  </div>
                </button>
              ))}
            </div>
          </div>
        </section>
      </main>

      <footer className="landing-footer">
        <div className="landing-footer-inner">
          <span className="landing-footer-copy">RollMetrics</span>
          <div className="legal-links-row" data-testid="signin-legal-links">
            <a
              href={GITHUB_URL}
              target="_blank"
              rel="noreferrer"
              className="legal-link-btn"
              style={{ textDecoration: "none" }}
            >
              GitHub
            </a>
            <span>·</span>
            <button
              type="button"
              className="legal-link-btn"
              onClick={() => onNavigateLegal("privacy")}
            >
              Privacy
            </button>
            <span>·</span>
            <button
              type="button"
              className="legal-link-btn"
              onClick={() => onNavigateLegal("terms")}
            >
              Terms
            </button>
          </div>
        </div>
      </footer>
    </div>
  );
}
