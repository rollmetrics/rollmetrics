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
  curriculumTitle: string;
  curriculumTime: string;
}

const GITHUB_URL = "https://github.com/rollmetrics/rollmetrics";
const CLIP_DURATION_SEC = 10.5;
const CLIP_START_CLOCK_SEC = 127; // 02:07

const LANDING_MOMENTS: LandingMoment[] = [
  {
    id: "m1",
    videoSec: 0.5,
    leftPct: 12,
    tone: "adv",
    label: "Sit-up guard elevation (02:07)",
    tag: "OPEN GUARD · 02:07",
    tagClass: "adv",
    title: "Sit-up guard elevation",
    detail: "Bruno Malfacine attacks off-balance from bottom",
    clock: "02:07",
    scoreA: [4, 2, 2],
    scoreB: [2, 2, 2],
    curriculumTitle: "Sit-up guard single-leg sweep",
    curriculumTime: "04:15",
  },
  {
    id: "m2",
    videoSec: 2.0,
    leftPct: 35,
    tone: "adv",
    label: "Sweep reversal to top (02:05)",
    tag: "SWEEP · 02:05",
    tagClass: "adv",
    title: "Sweep reversal to top",
    detail: "IBJJF Art. 4.6 (3s stabilization starts)",
    clock: "02:05",
    scoreA: [4, 2, 2],
    scoreB: [2, 2, 2],
    curriculumTitle: "Open-guard sweep finish & base control",
    curriculumTime: "08:20",
  },
  {
    id: "m3",
    videoSec: 5.0,
    leftPct: 62,
    tone: "win",
    label: "Top passing stabilization (02:02)",
    tag: "3s HOLD · 02:02",
    tagClass: "win",
    title: "Top passing stabilization",
    detail: "IBJJF Art. 4.1 (control verified over frames)",
    clock: "02:02",
    scoreA: [4, 2, 2],
    scoreB: [2, 2, 2],
    curriculumTitle: "Knee-cut & pant-grip stabilization",
    curriculumTime: "12:40",
  },
  {
    id: "m4",
    videoSec: 7.5,
    leftPct: 86,
    tone: "win",
    label: "Sweep awarded 4-4 tie (01:59)",
    tag: "+2 PTS · 01:59",
    tagClass: "win",
    title: "Sweep awarded (4-4 tie)",
    detail: "IBJJF Art. 4.6 (2 pts confirmed on board)",
    clock: "01:59",
    scoreA: [4, 2, 2],
    scoreB: [4, 2, 2],
    curriculumTitle: "Roosterweight top-game pressure passing",
    curriculumTime: "16:10",
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
  const [activeIdx, setActiveIdx] = useState(3);
  const [progressPct, setProgressPct] = useState(LANDING_MOMENTS[3].leftPct);
  const [liveClock, setLiveClock] = useState(LANDING_MOMENTS[3].clock);

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

            <div className="landing-output-list">
              <button
                type="button"
                className="landing-output-item interactive"
                data-testid="landing-active-moment-card"
                onClick={() => selectMoment((activeIdx + 1) % LANDING_MOMENTS.length)}
              >
                <span className={`landing-output-tag ${activeMoment.tagClass}`}>
                  {activeMoment.tag}
                </span>
                <div className="landing-output-text">
                  <strong>{activeMoment.title}</strong> · {activeMoment.detail}
                </div>
              </button>
              <div className="landing-output-item">
                <span className="landing-output-tag blue">CURRICULUM</span>
                <div className="landing-output-text">
                  <strong>{activeMoment.curriculumTitle}</strong> · {activeMoment.curriculumTime}
                </div>
              </div>
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
