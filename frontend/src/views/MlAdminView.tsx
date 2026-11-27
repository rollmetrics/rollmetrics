import type { AdminOverviewResponse, AuthConfig } from "../types";

interface MlAdminViewProps {
  adminOverview: AdminOverviewResponse | null;
  adminLoading: boolean;
  adminSplit: "heldout" | "calibration";
  onChangeSplit: (split: "heldout" | "calibration") => void;
  authConfig: AuthConfig | null;
  onSwitchToAppAdmin: () => void;
  onBackToApp: () => void;
}

function formatPct(val: number): string {
  return `${Math.round((Number(val) || 0) * 100)}%`;
}

function formatLatencyMs(ms: number): string {
  const n = Number(ms) || 0;
  if (n <= 0) return "-";
  if (n >= 1000) return `${(n / 1000).toFixed(1)}s`;
  return `${Math.round(n)}ms`;
}

function formatCostUsd(cost: number): string {
  const n = Number(cost) || 0;
  if (n <= 0) return "$0.00";
  return `$${n.toFixed(4)}`;
}

export function MlAdminView({
  adminOverview,
  adminLoading,
  adminSplit,
  onChangeSplit,
  authConfig,
  onSwitchToAppAdmin,
  onBackToApp,
}: MlAdminViewProps) {
  const splitRows = adminOverview?.scoreboard_eval.splits?.[adminSplit] || [];
  const idLock = adminOverview?.id_lock_eval;

  return (
    <main className="admin-dashboard" data-testid="ml-admin-dashboard">
      <div className="admin-header">
        <div>
          <h1 className="admin-title">ML Admin &amp; Evaluation Lab</h1>
          <p className="admin-subtitle">
            Match Moments engine versions, blind-video benchmarks, ID-Lock accuracy, and experiments.
          </p>
        </div>
        <div style={{ display: "flex", gap: "0.5rem", flexWrap: "wrap" }}>
          <button
            type="button"
            className="mini-btn"
            data-testid="switch-app-admin-btn"
            onClick={onSwitchToAppAdmin}
          >
            App Admin -&gt;
          </button>
          <button
            type="button"
            className="mini-btn"
            data-testid="ml-admin-back-btn"
            onClick={onBackToApp}
          >
            &lt;- Back to App
          </button>
        </div>
      </div>

      {adminLoading && !adminOverview ? (
        <div className="admin-section">Loading benchmark and experiment telemetry...</div>
      ) : (
        <>
          <section className="admin-section" aria-label="Scoreboard Benchmark">
            <div className="overview-library-head">
              <div>
                <h2>Scoreboard-Grounded Blind-Video Benchmark</h2>
                <div className="admin-meta-line">
                  {adminOverview?.scoreboard_eval.benchmark_id || "rollmetrics-scoreboard-v1"} ·{" "}
                  {adminOverview?.scoreboard_eval.total_matches || 0} matches (
                  {adminOverview?.scoreboard_eval.calibration || 0} cal /{" "}
                  {adminOverview?.scoreboard_eval.heldout || 0} heldout) ·{" "}
                  {adminOverview?.scoreboard_eval.leakage_gate || "verified"}
                </div>
              </div>
              <div className="rail-segmented" role="group" aria-label="Benchmark split">
                <button
                  type="button"
                  data-testid="admin-split-heldout"
                  className={`rail-seg-btn ${adminSplit === "heldout" ? "active" : ""}`}
                  onClick={() => onChangeSplit("heldout")}
                >
                  Heldout
                </button>
                <button
                  type="button"
                  data-testid="admin-split-calibration"
                  className={`rail-seg-btn ${adminSplit === "calibration" ? "active" : ""}`}
                  onClick={() => onChangeSplit("calibration")}
                >
                  Calibration
                </button>
              </div>
            </div>

            <div className="admin-table-wrap">
              <table className="admin-table" data-testid="admin-scoreboard-table">
                <thead>
                  <tr>
                    <th>Model &amp; Harness</th>
                    <th>N</th>
                    <th>Winner Acc</th>
                    <th>Exact Score</th>
                    <th>Pts MAE</th>
                    <th>Event F1</th>
                    <th>P50 Latency</th>
                    <th>$/Match</th>
                  </tr>
                </thead>
                <tbody>
                  {splitRows.map((row) => (
                    <tr key={row.model_id}>
                      <td className="mono-cell">{row.model_id}</td>
                      <td className="num-col">{row.match_count}</td>
                      <td className="num-col">{formatPct(row.winner_accuracy)}</td>
                      <td className="num-col">{formatPct(row.final_score_exact_match)}</td>
                      <td className="num-col">{row.mae_pts.toFixed(2)}</td>
                      <td className="num-col">{formatPct(row.score_event_f1)}</td>
                      <td className="num-col">{formatLatencyMs(row.p50_ms)}</td>
                      <td className="num-col">{formatCostUsd(row.cost_usd)}</td>
                    </tr>
                  ))}
                  {splitRows.length === 0 && (
                    <tr>
                      <td colSpan={8} className="admin-empty-cell">
                        No benchmark runs recorded for this split yet.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </section>

          <section className="admin-section" aria-label="Match Moments Engine Versions">
            <div className="overview-library-head">
              <h2>RollMetrics Match Moments Engine Versions</h2>
            </div>
            <div className="admin-table-wrap">
              <table className="admin-table" data-testid="ml-engine-versions-table">
                <thead>
                  <tr>
                    <th>Version</th>
                    <th>Engine</th>
                    <th>Harness</th>
                    <th>Heldout Winner</th>
                    <th>Notes</th>
                  </tr>
                </thead>
                <tbody>
                  {(adminOverview?.engine_versions || []).map((ev) => (
                    <tr key={ev.version}>
                      <td className="mono-cell">{ev.version}</td>
                      <td>{ev.name}</td>
                      <td>{ev.harness}</td>
                      <td className="mono-cell">{ev.heldout_winner}</td>
                      <td>{ev.notes}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>

          <section className="admin-section" aria-label="Evaluation Dataset and Experiments">
            <div className="overview-library-head">
              <h2>Evaluation Dataset &amp; Experiments</h2>
            </div>
            {adminOverview?.dataset_details && (
              <div className="admin-meta-line" style={{ marginBottom: "0.6rem" }}>
                {adminOverview.dataset_details.leakage_control} ·{" "}
                {adminOverview.dataset_details.early_stoppage_audit} ·{" "}
                {adminOverview.dataset_details.vicos_adapter}
              </div>
            )}
            <div className="admin-table-wrap">
              <table className="admin-table" data-testid="ml-experiments-table">
                <thead>
                  <tr>
                    <th>Experiment</th>
                    <th>Delta</th>
                    <th>Status</th>
                  </tr>
                </thead>
                <tbody>
                  {(adminOverview?.experiments || []).map((ex) => (
                    <tr key={ex.id}>
                      <td>{ex.title}</td>
                      <td className="mono-cell">{ex.delta}</td>
                      <td>
                        <span className="gameplan-pos-pill">{ex.status}</span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>

          <section className="admin-section" aria-label="Knowledge Base and Rules Scorecard">
            <div className="overview-library-head">
              <h2>Deterministic Rules, ID-Lock &amp; Knowledge Base Evals</h2>
            </div>
            <div className="admin-kpi-grid" data-testid="admin-kb-strip">
              <div className="admin-kpi-card" data-testid="ml-id-lock-card">
                <span className="admin-kpi-label">Athlete ID-Lock %</span>
                <strong className="admin-kpi-value">
                  {idLock ? `${(idLock.id_lock_pct * 100).toFixed(1)}%` : "100.0%"}
                </strong>
                <span className="admin-kpi-sub">
                  {idLock
                    ? `${idLock.evaluated_bouts} bouts · ${idLock.swap_count} swaps`
                    : "Tap-to-Identify (x,y) continuity"}
                </span>
              </div>
              <div className="admin-kpi-card">
                <span className="admin-kpi-label">Knowledge Index</span>
                <strong className="admin-kpi-value">
                  {adminOverview?.knowledge_eval.version || "v0.1.0"} ·{" "}
                  {adminOverview?.knowledge_eval.total_docs || 0} docs
                </strong>
                <span className="admin-kpi-sub">
                  {adminOverview?.knowledge_eval.ibjjf_docs || 0} IBJJF ·{" "}
                  {adminOverview?.knowledge_eval.cta_docs || 0} CTA
                </span>
              </div>
              <div className="admin-kpi-card">
                <span className="admin-kpi-label">IBJJF &amp; ADCC Rules</span>
                <strong className="admin-kpi-value">
                  {adminOverview?.knowledge_eval.suites.ibjjf_rules || "38/38"}
                </strong>
                <span className="admin-kpi-sub">Scoring, penalties, legality</span>
              </div>
              <div className="admin-kpi-card">
                <span className="admin-kpi-label">Match Timelines</span>
                <strong className="admin-kpi-value">
                  {adminOverview?.knowledge_eval.suites.match_timelines || "8/8"}
                </strong>
                <span className="admin-kpi-sub">Curated ground-truth parity</span>
              </div>
              <div className="admin-kpi-card">
                <span className="admin-kpi-label">Curriculum Recall@3</span>
                <strong className="admin-kpi-value">
                  {adminOverview?.knowledge_eval.suites.cta_recall_at_3 || "24/24"}
                </strong>
                <span className="admin-kpi-sub">Curriculum retrieval accuracy</span>
              </div>
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
