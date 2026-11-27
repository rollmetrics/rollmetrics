import type { AdminOverviewResponse, AuthConfig } from "../types";

interface AppAdminViewProps {
  adminOverview: AdminOverviewResponse | null;
  adminLoading: boolean;
  authConfig: AuthConfig | null;
  onOpenCurricula: () => void;
  onBackToApp: () => void;
}

const BENCHMARKS_GITHUB_URL =
  "https://github.com/rollmetrics/rollmetrics/tree/main/benchmarks";

export function AppAdminView({
  adminOverview,
  adminLoading,
  authConfig,
  onOpenCurricula,
  onBackToApp,
}: AppAdminViewProps) {
  const athletes = adminOverview?.athletes || adminOverview?.users_and_usage || [];
  const teams = adminOverview?.teams || [];
  const curricula = adminOverview?.curricula || [];
  const rulesets = (adminOverview?.rulesets || []).filter((r) => r.id !== "sparring");

  return (
    <main className="admin-dashboard" data-testid="admin-dashboard">
      <div className="admin-header">
        <div>
          <h1 className="admin-title">App Admin</h1>
          <p className="admin-subtitle">
            Athletes, teams, curricula, and active rulesets.
          </p>
        </div>
        <div style={{ display: "flex", gap: "0.5rem", flexWrap: "wrap", alignItems: "center" }}>
          <a
            href={BENCHMARKS_GITHUB_URL}
            target="_blank"
            rel="noreferrer"
            className="mini-btn"
            data-testid="github-benchmarks-link"
            style={{ textDecoration: "none" }}
          >
            Benchmarks on GitHub
          </a>
          <button
            type="button"
            className="mini-btn"
            data-testid="admin-back-btn"
            onClick={onBackToApp}
          >
            &lt;- Back to App
          </button>
        </div>
      </div>

      {adminLoading && !adminOverview ? (
        <div className="admin-section">Loading workspace administration data...</div>
      ) : (
        <>
          <section className="admin-section" aria-label="Athletes and Roles">
            <div className="overview-library-head">
              <h2>Athletes &amp; Members</h2>
            </div>
            <div className="admin-table-wrap">
              <table className="admin-table" data-testid="admin-users-table">
                <thead>
                  <tr>
                    <th style={{ width: "24%" }}>Athlete</th>
                    <th style={{ width: "10%" }}>Role</th>
                    <th style={{ width: "18%" }}>Teams</th>
                    <th style={{ width: "18%" }}>Curriculum</th>
                    <th style={{ width: "8%" }}>Matches</th>
                    <th style={{ width: "8%" }}>Uploads</th>
                    <th style={{ width: "14%" }}>Last Seen</th>
                  </tr>
                </thead>
                <tbody>
                  {athletes.map((u) => (
                    <tr key={u.email}>
                      <td className="mono-cell">
                        <div>{u.name || u.email}</div>
                        <div style={{ fontSize: "0.68rem", color: "var(--text-muted)" }}>
                          {u.email}
                        </div>
                      </td>
                      <td>
                        <span className="gameplan-pos-pill">{u.role || "member"}</span>
                      </td>
                      <td>{(u.teams || []).join(", ") || "-"}</td>
                      <td>{u.academy_name || "-"}</td>
                      <td className="num-col">{u.my_matches}</td>
                      <td className="num-col">{u.uploaded_videos}</td>
                      <td className="mono-cell">{u.last_seen || "-"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>

          <section className="admin-section" aria-label="Teams">
            <div className="overview-library-head">
              <h2>Teams &amp; Affiliations</h2>
            </div>
            <div className="admin-table-wrap">
              <table className="admin-table" data-testid="admin-teams-table">
                <thead>
                  <tr>
                    <th style={{ width: "26%" }}>Team</th>
                    <th style={{ width: "10%" }}>Members</th>
                    <th style={{ width: "18%" }}>Default Coach</th>
                    <th style={{ width: "28%" }}>Default Curriculum</th>
                    <th style={{ width: "18%" }}>Default Ruleset</th>
                  </tr>
                </thead>
                <tbody>
                  {teams.map((tm) => (
                    <tr key={tm.id}>
                      <td>{tm.name}</td>
                      <td className="num-col">{tm.member_count}</td>
                      <td>{tm.default_coach || "Head Coach"}</td>
                      <td>{tm.default_curriculum}</td>
                      <td>{tm.default_ruleset}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>

          <section className="admin-section" aria-label="Curricula">
            <div className="overview-library-head">
              <h2>Curricula</h2>
              <button
                type="button"
                className="mini-btn"
                data-testid="open-curricula-btn"
                onClick={onOpenCurricula}
              >
                Browse Curricula -&gt;
              </button>
            </div>
            <div className="admin-table-wrap">
              <table className="admin-table" data-testid="admin-curricula-table">
                <thead>
                  <tr>
                    <th style={{ width: "26%" }}>Curriculum</th>
                    <th style={{ width: "12%" }}>Visibility</th>
                    <th style={{ width: "10%" }}>Docs</th>
                    <th style={{ width: "12%" }}>Video</th>
                    <th style={{ width: "10%" }}>Text</th>
                    <th style={{ width: "30%" }}>Status</th>
                  </tr>
                </thead>
                <tbody>
                  {curricula.map((c) => (
                    <tr key={c.id}>
                      <td>{c.name}</td>
                      <td>
                        <span className="gameplan-pos-pill">{c.visibility}</span>
                      </td>
                      <td className="num-col">{c.doc_count}</td>
                      <td className="num-col">{c.video_backed_count}</td>
                      <td className="num-col">{c.text_only_count}</td>
                      <td>{c.status}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>

          <section className="admin-section" aria-label="Rulesets">
            <div className="overview-library-head">
              <h2>Rulesets</h2>
            </div>
            <div className="admin-table-wrap">
              <table className="admin-table" data-testid="admin-rulesets-table">
                <thead>
                  <tr>
                    <th style={{ width: "22%" }}>Ruleset</th>
                    <th style={{ width: "10%" }}>Status</th>
                    <th style={{ width: "12%" }}>Legality</th>
                    <th style={{ width: "10%" }}>Docs</th>
                    <th style={{ width: "10%" }}>Videos</th>
                    <th style={{ width: "36%" }}>Coverage</th>
                  </tr>
                </thead>
                <tbody>
                  {rulesets.map((rs) => (
                    <tr key={rs.id}>
                      <td>{rs.name}</td>
                      <td>
                        <span className="gameplan-pos-pill">{rs.status}</span>
                      </td>
                      <td className="num-col">{rs.legality_cases}</td>
                      <td className="num-col">{rs.rule_docs}</td>
                      <td className="num-col">{rs.official_videos}</td>
                      <td>{rs.notes}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        </>
      )}

      <footer
        style={{
          fontFamily: "var(--font-mono)",
          fontSize: "0.7rem",
          color: "var(--text-muted)",
          paddingTop: "0.25rem",
        }}
      >
        RollMetrics v{adminOverview?.app_version || authConfig?.app_version || "0.3.0"} ·{" "}
        {adminOverview?.build_sha || authConfig?.build_sha || "local"} ·{" "}
        {adminOverview?.environment || authConfig?.environment || "local"}
      </footer>
    </main>
  );
}
