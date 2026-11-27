import type { AdminOverviewResponse, AuthConfig } from "../types";

interface AppAdminViewProps {
  adminOverview: AdminOverviewResponse | null;
  adminLoading: boolean;
  authConfig: AuthConfig | null;
  onSwitchToMlAdmin: () => void;
  onBackToApp: () => void;
}

export function AppAdminView({
  adminOverview,
  adminLoading,
  authConfig,
  onSwitchToMlAdmin,
  onBackToApp,
}: AppAdminViewProps) {
  const athletes = adminOverview?.athletes || adminOverview?.users_and_usage || [];
  const teams = adminOverview?.teams || [];
  const curricula = adminOverview?.curricula || [];
  const rulesets = adminOverview?.rulesets || [];
  const jobs = adminOverview?.upload_jobs || [];

  return (
    <main className="admin-dashboard" data-testid="admin-dashboard">
      <div className="admin-header">
        <div>
          <h1 className="admin-title">App Admin</h1>
          <p className="admin-subtitle">
            Athletes, teams, curricula, rulesets, and upload/analysis jobs.
          </p>
        </div>
        <div style={{ display: "flex", gap: "0.5rem", flexWrap: "wrap", alignItems: "center" }}>
          {typeof adminOverview?.max_daily_cloud_usd === "number" && (
            <span className="gameplan-pos-pill" data-testid="admin-budget-pill">
              Cloud Spend ${(adminOverview.daily_cloud_spend_usd || 0).toFixed(2)} / $
              {adminOverview.max_daily_cloud_usd.toFixed(2)}
            </span>
          )}
          <button
            type="button"
            className="mini-btn"
            data-testid="switch-ml-admin-btn"
            onClick={onSwitchToMlAdmin}
          >
            ML Admin -&gt;
          </button>
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
                    <th>Athlete</th>
                    <th>Role</th>
                    <th>Teams</th>
                    <th>Curriculum</th>
                    <th>My Matches</th>
                    <th>Uploaded</th>
                    <th>Analyses</th>
                    <th>Last Seen</th>
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
                      <td className="num-col">{u.analyses_run}</td>
                      <td className="mono-cell">{u.last_seen || "-"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>

          <section className="admin-section" aria-label="Upload and Analysis Jobs">
            <div className="overview-library-head">
              <h2>Upload &amp; Analysis Jobs</h2>
            </div>
            <div className="admin-table-wrap">
              <table className="admin-table" data-testid="admin-jobs-table">
                <thead>
                  <tr>
                    <th>Job</th>
                    <th>File</th>
                    <th>Owner</th>
                    <th>Ruleset</th>
                    <th>Status</th>
                    <th>Created</th>
                  </tr>
                </thead>
                <tbody>
                  {jobs.map((job) => (
                    <tr key={job.job_id}>
                      <td className="mono-cell">{job.job_id}</td>
                      <td>{job.filename}</td>
                      <td className="mono-cell">{job.owner_email}</td>
                      <td>
                        <span className="gameplan-pos-pill">{job.ruleset}</span>
                      </td>
                      <td>
                        <span className="gameplan-pos-pill">{job.status}</span>
                      </td>
                      <td className="mono-cell">{job.created_at}</td>
                    </tr>
                  ))}
                  {jobs.length === 0 && (
                    <tr>
                      <td colSpan={6} className="admin-empty-cell">
                        No upload or analysis jobs recorded yet.
                      </td>
                    </tr>
                  )}
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
                    <th>Team</th>
                    <th>Members</th>
                    <th>Default Curriculum</th>
                    <th>Default Ruleset</th>
                  </tr>
                </thead>
                <tbody>
                  {teams.map((tm) => (
                    <tr key={tm.id}>
                      <td>{tm.name}</td>
                      <td className="num-col">{tm.member_count}</td>
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
            </div>
            <div className="admin-table-wrap">
              <table className="admin-table" data-testid="admin-curricula-table">
                <thead>
                  <tr>
                    <th>Curriculum</th>
                    <th>Visibility</th>
                    <th>Total Docs</th>
                    <th>Video-Backed</th>
                    <th>Text-Only</th>
                    <th>Status</th>
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
                    <th>Ruleset</th>
                    <th>Status</th>
                    <th>Legality Cases</th>
                    <th>Rule Docs</th>
                    <th>Official Videos</th>
                    <th>Coverage</th>
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
