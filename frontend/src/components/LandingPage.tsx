import type { RefObject } from "react";

interface LandingPageProps {
  gisButtonRef: RefObject<HTMLDivElement>;
  authError: string | null;
  formatAuthGateError: (raw: string) => string;
  onNavigateLegal: (mode: "privacy" | "terms") => void;
  onDemoLogin?: () => void;
  demoLoginBusy?: boolean;
}

const GITHUB_URL = "https://github.com/rollmetrics/rollmetrics";

export function LandingPage({
  gisButtonRef,
  authError,
  formatAuthGateError,
  onNavigateLegal,
  onDemoLogin,
  demoLoginBusy,
}: LandingPageProps) {
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
                  <button
                    type="button"
                    className="landing-secondary-btn"
                    data-testid="demo-sebastian-btn"
                    disabled={demoLoginBusy}
                    onClick={onDemoLogin}
                  >
                    {demoLoginBusy ? "Opening Demo..." : "Try Demo (Sebastian)"}
                  </button>
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
              <img
                src="/og-card.png"
                alt="Sample Brazilian Jiu-Jitsu match video frame"
                className="landing-video-thumb"
              />
              <span className="landing-play-badge">▶ Match Video · 0.4x Replay</span>
            </div>

            <div className="landing-scoreboard">
              <div className="landing-score-side">
                <span className="landing-score-name">Athlete A</span>
                <div className="landing-score-nums">
                  <span className="pts">5</span>
                  <span className="adv">1</span>
                  <span className="pen">0</span>
                </div>
              </div>
              <div className="landing-score-clock">02:41</div>
              <div className="landing-score-side right">
                <div className="landing-score-nums">
                  <span className="pts">2</span>
                  <span className="adv">0</span>
                  <span className="pen">1</span>
                </div>
                <span className="landing-score-name">Athlete B</span>
              </div>
            </div>

            <div className="landing-scrubber" aria-hidden="true">
              <div className="landing-scrubber-fill" />
              <span className="landing-pin adv" style={{ left: "22%" }} />
              <span className="landing-pin win" style={{ left: "48%" }} />
              <span className="landing-pin win" style={{ left: "64%" }} />
              <span className="landing-pin pen" style={{ left: "82%" }} />
            </div>

            <div className="landing-output-list">
              <div className="landing-output-item">
                <span className="landing-output-tag win">+3 PTS · 02:19</span>
                <div className="landing-output-text">
                  <strong>Guard pass</strong> · IBJJF Art. 4.4 (3s hold)
                </div>
              </div>
              <div className="landing-output-item">
                <span className="landing-output-tag">GIF</span>
                <div className="landing-output-text">
                  <strong>0.4x slow-motion clip</strong> ready to replay or share
                </div>
              </div>
              <div className="landing-output-item">
                <span className="landing-output-tag blue">CURRICULUM</span>
                <div className="landing-output-text">
                  <strong>Knee-cut frame recovery</strong> · 12:40
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
