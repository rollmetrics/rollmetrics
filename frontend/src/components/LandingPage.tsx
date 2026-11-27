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
  leftPct: string;
  tone: "adv" | "win" | "pen";
  label: string;
  tag: string;
  tagClass: string;
  title: string;
  detail: string;
  clock: string;
  scoreA: [number, number, number];
  scoreB: [number, number, number];
  gifNote: string;
  curriculumTitle: string;
  curriculumTime: string;
}

const GITHUB_URL = "https://github.com/rollmetrics/rollmetrics";

const LANDING_MOMENTS: LandingMoment[] = [
  {
    id: "guard-angle",
    videoSec: 1.0,
    leftPct: "16%",
    tone: "adv",
    label: "Closed guard angle (+1 ADV at 04:42)",
    tag: "+1 ADV · 04:42",
    tagClass: "adv",
    title: "Closed guard angle",
    detail: "IBJJF Art. 5.3 (submission threat)",
    clock: "04:42",
    scoreA: [0, 1, 0],
    scoreB: [0, 0, 0],
    gifNote: "0:01 clip ready to replay or share",
    curriculumTitle: "Closed guard hip angle & collar break",
    curriculumTime: "04:15",
  },
  {
    id: "sweep-top",
    videoSec: 2.4,
    leftPct: "40%",
    tone: "win",
    label: "Sweep to top (+2 PTS at 03:18)",
    tag: "+2 PTS · 03:18",
    tagClass: "win",
    title: "Sweep to top",
    detail: "IBJJF Art. 4.3 (3s hold)",
    clock: "03:18",
    scoreA: [2, 1, 0],
    scoreB: [0, 0, 0],
    gifNote: "0:02 clip ready to replay or share",
    curriculumTitle: "Underhook sweep finish & base control",
    curriculumTime: "08:20",
  },
  {
    id: "guard-pass",
    videoSec: 3.9,
    leftPct: "65%",
    tone: "win",
    label: "Guard pass (+3 PTS at 02:19)",
    tag: "+3 PTS · 02:19",
    tagClass: "win",
    title: "Guard pass",
    detail: "IBJJF Art. 4.4 (3s hold)",
    clock: "02:19",
    scoreA: [5, 1, 0],
    scoreB: [2, 0, 1],
    gifNote: "0:04 clip ready to replay or share",
    curriculumTitle: "Knee-cut frame recovery",
    curriculumTime: "12:40",
  },
  {
    id: "triangle-finish",
    videoSec: 5.1,
    leftPct: "85%",
    tone: "pen",
    label: "Triangle choke finish (SUB at 01:04)",
    tag: "SUB · 01:04",
    tagClass: "pen",
    title: "Triangle choke finish",
    detail: "IBJJF Art. 1.3 (tap confirmed)",
    clock: "01:04",
    scoreA: [5, 1, 0],
    scoreB: [2, 0, 1],
    gifNote: "0:05 clip ready to replay or share",
    curriculumTitle: "Triangle angle cut & shin lock",
    curriculumTime: "16:05",
  },
];

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
  const [slowMo, setSlowMo] = useState(false);

  const activeMoment = LANDING_MOMENTS[activeIdx];

  const selectMoment = (idx: number) => {
    const next = LANDING_MOMENTS[idx];
    setActiveIdx(idx);
    if (previewVideoRef.current) {
      previewVideoRef.current.currentTime = next.videoSec;
    }
  };

  const toggleSlowMo = () => {
    const next = !slowMo;
    setSlowMo(next);
    if (previewVideoRef.current) {
      previewVideoRef.current.playbackRate = next ? 0.4 : 1.0;
    }
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
            <div className="landing-eyebrow">Open-Source Jiu-Jitsu Film Study</div>
            <h1 className="landing-h1">
              Score matches, cut GIFs, and coach from your gym&apos;s library.
            </h1>
            <p className="landing-lead">
              IBJJF scoring, slow-motion clips, and private curriculum links. Run offline on your
              own hardware or sign in to the hosted beta.
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
              <span>IBJJF v6.1 · Gi</span>
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
                className="landing-video-thumb"
                aria-label="Sample Brazilian Jiu-Jitsu match clip"
              />
              <button
                type="button"
                className={`landing-play-badge${slowMo ? " active" : ""}`}
                data-testid="landing-slowmo-toggle"
                aria-pressed={slowMo}
                onClick={toggleSlowMo}
              >
                {slowMo ? "▶ 0.4x Slow-Mo · Tap for 1x" : "▶ Match Video · 0.4x Replay"}
              </button>
            </div>

            <div className="landing-scoreboard">
              <div className="landing-score-side">
                <span className="landing-score-name">Athlete A</span>
                <div className="landing-score-nums">
                  <span className="pts">{activeMoment.scoreA[0]}</span>
                  <span className="adv">{activeMoment.scoreA[1]}</span>
                  <span className="pen">{activeMoment.scoreA[2]}</span>
                </div>
              </div>
              <div className="landing-score-clock" data-testid="landing-score-clock">
                {activeMoment.clock}
              </div>
              <div className="landing-score-side right">
                <div className="landing-score-nums">
                  <span className="pts">{activeMoment.scoreB[0]}</span>
                  <span className="adv">{activeMoment.scoreB[1]}</span>
                  <span className="pen">{activeMoment.scoreB[2]}</span>
                </div>
                <span className="landing-score-name">Athlete B</span>
              </div>
            </div>

            <div
              className="landing-scrubber"
              role="group"
              aria-label="Interactive match moment markers"
            >
              <div
                className="landing-scrubber-fill"
                style={{ width: activeMoment.leftPct }}
              />
              {LANDING_MOMENTS.map((m, idx) => (
                <button
                  key={m.id}
                  type="button"
                  className={`landing-pin ${m.tone}${idx === activeIdx ? " active" : ""}`}
                  style={{ left: m.leftPct }}
                  data-testid={`landing-pin-${idx}`}
                  aria-label={m.label}
                  aria-pressed={idx === activeIdx}
                  onClick={() => selectMoment(idx)}
                />
              ))}
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
                <span className="landing-output-tag">GIF</span>
                <div className="landing-output-text">
                  <strong>{slowMo ? "0.4x slow-motion active" : "0.4x slow-motion clip"}</strong> ·{" "}
                  {activeMoment.gifNote}
                </div>
              </div>
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
