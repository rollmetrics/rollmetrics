import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { setStoredToken } from "../api";
import { App } from "../App";
import {
  EmbeddedReferenceVideo,
  parseReferenceMedia,
  parseTimestampParam,
} from "../components/EmbeddedReferenceVideo";
import type { ShowcaseMatch } from "../types";

const MOCK_MATCHES: ShowcaseMatch[] = [
  {
    id: "mc9_semifinal",
    pill: "Mathewson vs. Dube",
    title: "Mastermind Cup 9 · Semifinal",
    belt: "white",
    belt_label: "White Belt · Gi · Master",
    bucket: "my_matches",
    owner_email: "coach@example.com",
    regulation_sec: 300,
    video_duration_sec: 224.7,
    video_start_sec: 16,
    video_end_sec: 185,
    clock_anchors: [
      [16, 300],
      [185, 131],
    ],
    athlete_a: { name: "Kory Mathewson", belt: "white", gi: "white" },
    athlete_b: { name: "Michael Dube", belt: "white", gi: "blue" },
    winner: "A",
    win_label: "SUB",
    events: [
      {
        id: "sf-1",
        video_sec: 51,
        time_str: "00:51",
        clock_str: "04:25",
        actor: "A",
        call: "GUARD PULL",
        detail: "Closed Guard",
        rule: "IBJJF Art. 6.3.2 (J)",
        cta_url: "https://www.youtube.com/watch?v=VWzT1iUMvEc&t=2967s",
        pts_a: 0,
        adv_a: 0,
        pen_a: 0,
        pts_b: 0,
        adv_b: 0,
        pen_b: 0,
      },
      {
        id: "sf-2",
        video_sec: 67,
        time_str: "01:07",
        clock_str: "04:09",
        actor: "B",
        call: "+1 PEN",
        detail: "Cuff Grip",
        rule: "IBJJF Art. 6.3.2 (F)",
        rule_info: {
          article: "IBJJF Art. 6.3.2 (F)",
          title: "Minor Technical Foul: Fingers Inside Sleeve or Pant Cuff",
          excerpt: "Placing fingers inside the opponent's sleeve or pant cuff is a technical penalty.",
          rulebook_url: "https://ibjjf.com/books-videos",
          ibjjf_video_url: "https://www.youtube.com/watch?v=RAwwWq7cI_o",
          ibjjf_video_title: "IBJJF 10. Disciplinary Penalties",
        },
        cta_url: "https://www.youtube.com/watch?v=4-7AYrBCOvQ&t=4797s",
        pts_a: 0,
        adv_a: 0,
        pen_a: 0,
        pts_b: 0,
        adv_b: 0,
        pen_b: 1,
      },
      {
        id: "sf-4",
        video_sec: 185,
        time_str: "03:05",
        clock_str: "02:11",
        actor: "A",
        call: "SUBMISSION",
        detail: "Armbar",
        rule: "IBJJF Art. 2.2.1",
        cta_url: "https://www.youtube.com/watch?v=4-7AYrBCOvQ&t=122s",
        pts_a: 0,
        adv_a: 0,
        pen_a: 0,
        pts_b: 0,
        adv_b: 0,
        pen_b: 1,
        is_finish: true,
      },
    ],
  },
  {
    id: "caio_vitor_2014",
    pill: "Terra vs. Oliveira",
    title: "2014 Chicago Open · Absolute Final",
    belt: "black",
    belt_label: "Black Belt · Gi · Adult",
    bucket: "film_study",
    owner_email: "",
    regulation_sec: 600,
    video_duration_sec: 137.3,
    video_start_sec: 0,
    video_end_sec: 115,
    athlete_a: { name: "Caio Terra", belt: "black", gi: "blue" },
    athlete_b: { name: "Vitor Oliveira", belt: "black", gi: "white" },
    winner: "B",
    win_label: "SUB",
    events: [
      {
        id: "ct-2",
        video_sec: 70.0,
        time_str: "01:10",
        clock_str: "08:55",
        actor: "A",
        call: "+2 PTS",
        detail: "Single-Leg Sweep",
        rule: "IBJJF Art. 4.6",
        cta_url: "https://www.youtube.com/watch?v=4-7AYrBCOvQ&t=3242s",
        pts_a: 2,
        adv_a: 0,
        pen_a: 1,
        pts_b: 0,
        adv_b: 0,
        pen_b: 0,
        control_3s: true,
      },
      {
        id: "ct-4",
        video_sec: 82.5,
        time_str: "01:22",
        clock_str: "08:43",
        actor: "B",
        call: "+1 ADV",
        detail: "Turtle Breakdown",
        rule: "IBJJF Art. 5.2",
        cta_url: "https://www.youtube.com/watch?v=xp6xTVThYAo&t=1272s",
        pts_a: 2,
        adv_a: 0,
        pen_a: 1,
        pts_b: 2,
        adv_b: 1,
        pen_b: 0,
        hold_sec: 1.8,
      },
      {
        id: "ct-6",
        video_sec: 114.0,
        time_str: "01:54",
        clock_str: "08:11",
        actor: "B",
        call: "SUBMISSION",
        detail: "Bow-and-Arrow Choke",
        rule: "IBJJF Art. 2.2.1",
        cta_url: "https://www.youtube.com/watch?v=xp6xTVThYAo&t=2150s",
        pts_a: 2,
        adv_a: 0,
        pen_a: 1,
        pts_b: 2,
        adv_b: 1,
        pen_b: 0,
        is_finish: true,
      },
    ],
  },
];

describe("RollMetrics Single-Screen Video Telemetry MVP", () => {
  beforeEach(() => {
    window.history.pushState({}, "", "/");
    setStoredToken("");
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.includes("/api/auth/config")) {
          return new Response(
            JSON.stringify({
              environment: "local",
              google_client_id: "",
              allow_local_dev_bypass: true,
              auth_required: false,
              public_url: "https://example.com",
              default_profile: "local",
            }),
            { status: 200 },
          );
        }
        if (url.includes("/api/auth/me")) {
          return new Response(
            JSON.stringify({
              email: "coach@example.com",
              name: "Kory Mathewson",
              picture: "",
              auth_mode: "local_bypass",
              role: "admin",
              is_admin: true,
            }),
            { status: 200 },
          );
        }
        if (url.includes("/api/rules/ibjjf-v6-1")) {
          return new Response(
            JSON.stringify({
              version: "v6.1",
              title: "IBJJF Rulebook v6.1 (Plaintext Reference)",
              count: 2,
              articles: [
                {
                  id: "ibjjf-1",
                  article: "IBJJF Art. 6.3.2 (F)",
                  title: "Fingers Inside Sleeve or Pant Cuff",
                  text: "Placing fingers inside the opponent's sleeve or pant cuff is a technical penalty.",
                  source_url: "https://ibjjf.com/books-videos",
                },
                {
                  id: "ibjjf-2",
                  article: "IBJJF Art. 4.4",
                  title: "Guard Pass (3 Points)",
                  text: "Stabilize past the guard for 3.0 seconds.",
                  source_url: "https://ibjjf.com/books-videos",
                },
              ],
            }),
            { status: 200 },
          );
        }
        if (url.includes("/api/me/profile") && init?.method === "PUT") {
          const body = JSON.parse(String(init.body || "{}")) as Record<string, unknown>;
          return new Response(
            JSON.stringify({
              email: "coach@example.com",
              name: body.name || "Kory Mathewson",
              picture: body.picture || "",
              belt: body.belt || "blue",
              academy: body.academy || "Plateau BJJ",
              coach: body.coach || "Chris B",
              academy_id: body.academy_id || "osjj-roadmap",
              academy_name: body.academy_name || "Open Source Curriculum",
              auth_mode: "local_bypass",
              role: "admin",
              is_admin: true,
            }),
            { status: 200 },
          );
        }
        if (url.includes("/api/team/roster")) {
          return new Response(
            JSON.stringify({
              roster: [
                {
                  match_id: "mc9_semifinal",
                  title: "Mastermind Cup 9 · Semifinal",
                  owner_email: "coach@example.com",
                  athlete_name: "Kory Mathewson",
                  belt_label: "White Belt · Gi · Master",
                  ruleset: "ibjjf_v6",
                  review_status: "needs_review",
                  pinned_drills: [],
                  created_at: "2026-11-27 12:00 UTC",
                },
              ],
            }),
            { status: 200 },
          );
        }
        if (url.includes("/review") && init?.method === "POST") {
          const body = JSON.parse(String(init.body || "{}")) as Record<string, unknown>;
          return new Response(
            JSON.stringify({
              match_id: "mc9_semifinal",
              review_status: body.review_status || "reviewed",
              pinned_drills: body.pin_drill ? [body.pin_drill] : [],
            }),
            { status: 200 },
          );
        }
        if (url.includes("/moments") && init?.method === "PUT") {
          const body = JSON.parse(String(init.body || "{}")) as { events?: unknown[] };
          return new Response(
            JSON.stringify({
              match: { ...MOCK_MATCHES[0], events: body.events || MOCK_MATCHES[0].events },
              feedback_logged: true,
            }),
            { status: 200 },
          );
        }
        if (url.includes("/api/academies/me/references/auto-tag")) {
          return new Response(
            JSON.stringify({
              title: "Knee Cut Pass Details",
              position: "guard_passing",
              belt: "blue",
              tags: ["guard_passing"],
              summary: "Auto-tagged guard passing study reference.",
            }),
            { status: 200 },
          );
        }
        if (url.includes("/api/share/") && init?.method === "DELETE") {
          return new Response(JSON.stringify({ ok: true, token: "tok_share_abc" }), { status: 200 });
        }
        if (url.includes("/api/matches/") && init?.method === "DELETE") {
          return new Response(JSON.stringify({ ok: true, match_id: "custom_1" }), { status: 200 });
        }
        if (url.includes("/api/me/coaching-summary")) {
          return new Response(
            JSON.stringify({
              user_email: "coach@example.com",
              match_count: 2,
              record: { wins: 2, losses: 0, draws: 0, subs: 1, decisions: 1, label: "2-0 (1 SUB, 1 DEC)" },
              points_scored: 0,
              points_allowed: 0,
              recurring_positions: ["Closed Guard", "Armbar"],
              summary_line: "2 matches logged (2-0 (1 SUB, 1 DEC)), 0 points allowed.",
              top_cta_priorities: [
                {
                  title: "Closed Guard Off-Balance & High Diamond",
                  cue: "Chain off-balance directly into high diamond.",
                  url: "https://youtu.be/KCJ54kkfoGA?t=15",
                },
              ],
            }),
            { status: 200 },
          );
        }
        if (url.includes("/api/local/hardware")) {
          return new Response(
            JSON.stringify({
              offline_ready: true,
              ollama_reachable: true,
              installed_models: ["gemma4:e2b-it-q4_K_M"],
              recommended_model: "gemma4:e2b-it-q4_K_M",
              machine: "arm64",
              ram_gb: 16,
            }),
            { status: 200 },
          );
        }
        if (url.includes("/api/local/folder/scan")) {
          return new Response(
            JSON.stringify({
              folder_path: "/Users/test/Videos/BJJ",
              count: 1,
              videos: [
                {
                  path: "/Users/test/Videos/BJJ/local_roll.mp4",
                  filename: "local_roll.mp4",
                  size_mb: 14.2,
                  duration_sec: 180,
                  analyzed: false,
                },
              ],
            }),
            { status: 200 },
          );
        }
        if (url.includes("/api/local/folder/analyze")) {
          return new Response(
            JSON.stringify({
              match: {
                ...MOCK_MATCHES[0],
                id: "usr_local_1",
                pill: "local_roll",
                title: "local_roll",
              },
              snapshot: {},
            }),
            { status: 200 },
          );
        }
        if (url.includes("/api/local/academy/import-folder")) {
          return new Response(
            JSON.stringify({
              folder_path: "/Users/test/Notes",
              academy_id: "cta",
              academy_name: "Caio Terra Association",
              imported_count: 3,
            }),
            { status: 200 },
          );
        }
        if (url.includes("/api/uploads/init")) {
          return new Response(
            JSON.stringify({
              upload_id: "upl_test123",
              upload_strategy: "multipart",
              upload_url: "/api/analyze/upload",
              method: "POST",
              headers: {},
              gcs_object: "",
            }),
            { status: 200 },
          );
        }
        if (url.includes("/api/uploads/google-drive")) {
          return new Response(
            JSON.stringify({
              match: {
                ...MOCK_MATCHES[0],
                id: "usr_drive_1",
                pill: "Drive Finals",
                title: "Drive Finals",
              },
              snapshot: {},
            }),
            { status: 200 },
          );
        }
        if (url.includes("/api/share/tok_share_abc/highlight")) {
          return new Response(
            JSON.stringify({
              filename: "slowmo_shared.mp4",
              url: "/api/share/tok_share_abc/media/slowmo_shared.mp4",
            }),
            { status: 200 },
          );
        }
        if (url.includes("/api/share/tok_share_abc")) {
          return new Response(
            JSON.stringify({
              token: "tok_share_abc",
              academy_name: "Open Source Jiu-Jitsu & Roadmap",
              match: {
                ...MOCK_MATCHES[0],
                video_url: "/api/share/tok_share_abc/video",
                poster_url: "/api/share/tok_share_abc/poster",
              },
            }),
            { status: 200 },
          );
        }
        if (url.includes("/api/matches/") && url.endsWith("/share") && init?.method === "POST") {
          return new Response(
            JSON.stringify({
              token: "tok_share_abc",
              match_id: "mc9_semifinal",
              share_path: "/share/tok_share_abc",
              share_url: "https://example.com/share/tok_share_abc",
            }),
            { status: 200 },
          );
        }
        if (url.includes("/api/academies/me/references")) {
          if (init?.method === "POST") {
            return new Response(
              JSON.stringify({
                reference: {
                  id: "ref-new-1",
                  title: "Knee Cut Pass",
                  position: "open_guard",
                  source_url: "https://example.com/knee-cut",
                  notes: "Pin the far hip first",
                },
              }),
              { status: 200 },
            );
          }
          return new Response(
            JSON.stringify({
              academy_id: "cta",
              academy_name: "Caio Terra Association",
              references: [
                {
                  id: "ref-1",
                  title: "Closed Guard Overhook",
                  position: "closed_guard",
                },
              ],
            }),
            { status: 200 },
          );
        }
        if (url.includes("/api/academies/me")) {
          if (init?.method === "PUT") {
            const body = JSON.parse(String(init.body || "{}"));
            return new Response(
              JSON.stringify({
                academy_id: "alliance-mtl",
                academy_name: body.academy_name || "Alliance Montreal",
              }),
              { status: 200 },
            );
          }
          return new Response(
            JSON.stringify({
              academy_id: "cta",
              academy_name: "Caio Terra Association",
              reference_count: 1,
            }),
            { status: 200 },
          );
        }
        if (url.includes("/api/analyze/url") || url.includes("/api/analyze/upload")) {
          return new Response(
            JSON.stringify({
              video_url: "/api/media/files/uploaded.mp4",
              snapshot: {
                winner: "A",
                win_method: "points",
                division: {
                  belt: "blue",
                  uniform: "gi",
                  duration_sec: 360,
                  athlete_a_name: "Kory Mathewson",
                  athlete_b_name: "Test Opponent",
                },
                timeline: [
                  {
                    event_id: "u1",
                    actor: "A",
                    raw_kind: "takedown",
                    timestamp_sec: 30,
                    awarded_points: 2,
                    rule_article: "IBJJF Art. 4.1",
                  },
                ],
              },
            }),
            { status: 200 },
          );
        }
        if (url.includes("/api/film-study/summary")) {
          return new Response(
            JSON.stringify({
              focus_athlete: "Caio Terra",
              match_count: 1,
              record_label: "0-1 (Black Belt Open)",
              combined_points_for: 2,
              combined_points_against: 2,
              summary_text:
                "1 film-study match featuring Caio Terra showing single-leg sweep timing and collar defense.",
              a_game_patterns: ["Single-Leg Sweep", "Closed Guard"],
              coaching_takeaways: [
                "Secure a collar or sleeve grip before sitting to guard.",
              ],
              cta_links: [
                {
                  title: "Single-Leg Sweep & Top Stabilization",
                  url: "https://www.youtube.com/watch?v=4-7AYrBCOvQ&t=3242s",
                },
              ],
              match_ids: ["caio_vitor_2014"],
            }),
            { status: 200 },
          );
        }
        if (url.includes("/api/admin/overview")) {
          return new Response(
            JSON.stringify({
              environment: "local",
              app_version: "0.1.0",
              build_sha: "abc1234",
              scoreboard_eval: {
                benchmark_id: "rollmetrics-scoreboard-v1",
                evaluated_at: "2026-11-26T20:00:00Z",
                total_matches: 122,
                calibration: 60,
                heldout: 62,
                leakage_gate: "verified",
                splits: {
                  heldout: [
                    {
                      model_id: "gemini-4-hydrogen-agentic",
                      match_count: 10,
                      winner_accuracy: 0.9,
                      final_score_exact_match: 0.6,
                      mae_pts: 1.1,
                      mae_adv: 0.2,
                      mae_pen: 0.1,
                      score_event_f1: 0.72,
                      p50_ms: 4820,
                      cost_usd: 0.014,
                    },
                  ],
                  calibration: [
                    {
                      model_id: "gemini-4-hydrogen-agentic",
                      match_count: 10,
                      winner_accuracy: 0.9,
                      final_score_exact_match: 0.7,
                      mae_pts: 0.9,
                      mae_adv: 0.1,
                      mae_pen: 0.1,
                      score_event_f1: 0.78,
                      p50_ms: 4610,
                      cost_usd: 0.013,
                    },
                  ],
                },
              },
              knowledge_eval: {
                version: "v0.1.0",
                total_docs: 231,
                ibjjf_docs: 56,
                cta_docs: 175,
                suites: {
                  ibjjf_rules: "38/38",
                  match_timelines: "8/8",
                  cta_recall_at_3: "24/24",
                },
              },
              users_and_usage: [
                {
                  email: "coach@example.com",
                  name: "Kory Mathewson",
                  role: "admin",
                  teams: ["Plateau BJJ"],
                  academy_name: "Open Source Jiu-Jitsu & Roadmap",
                  my_matches: 2,
                  film_study: 6,
                  uploaded_videos: 1,
                  analyses_run: 3,
                  live_sessions: 1,
                  last_seen: "2026-11-26T20:00:00Z",
                },
              ],
              teams: [
                {
                  id: "plateau-bjj",
                  name: "Plateau BJJ",
                  member_count: 12,
                  default_curriculum: "Open Source Jiu-Jitsu & Roadmap",
                  default_ruleset: "IBJJF v6.1",
                },
              ],
              curricula: [
                {
                  id: "osjj-roadmap",
                  name: "Open Source Jiu-Jitsu & Roadmap",
                  visibility: "public",
                  doc_count: 18,
                  video_backed_count: 18,
                  text_only_count: 0,
                  status: "active",
                },
              ],
              rulesets: [
                {
                  id: "ibjjf_v6",
                  name: "IBJJF v6.1",
                  status: "active",
                  legality_cases: 38,
                  rule_docs: 56,
                  official_videos: 24,
                  notes: "Full Gi & No-Gi scoring",
                },
                {
                  id: "adcc",
                  name: "ADCC",
                  status: "active",
                  legality_cases: 12,
                  rule_docs: 10,
                  official_videos: 4,
                  notes: "No-points first half, -1 guard pull",
                },
              ],
              upload_jobs: [
                {
                  job_id: "upl_demo123",
                  filename: "match.mov",
                  owner_email: "coach@example.com",
                  status: "completed",
                  ruleset: "adcc",
                  created_at: "2026-11-27 12:00 UTC",
                },
              ],
              engine_versions: [
                {
                  version: "v0.3.0",
                  name: "Hydrogen Agentic Mat-Crop",
                  harness: "480p dynamic crop + anchor",
                  heldout_winner: "100.0%",
                  notes: "Zero A/B swaps",
                },
              ],
              experiments: [
                {
                  id: "exp-1",
                  title: "Dynamic Mat Crop + Anchor",
                  delta: "+20.0% heldout winner",
                  status: "shipped",
                },
              ],
              dataset_details: {
                benchmark_id: "rollmetrics-scoreboard-v1",
                total_matches: 122,
                calibration_matches: 41,
                heldout_matches: 81,
                cross_split_video_overlap: 0,
                leakage_control: "strict_blackout_drawbox",
                early_stoppage_audit: "78 bouts audited",
                vicos_adapter: "research-only CC BY-NC-SA 4.0",
              },
              id_lock_eval: {
                id_lock_pct: 1.0,
                evaluated_bouts: 20,
                swap_count: 0,
                matching_gi_bouts: 6,
              },
              daily_cloud_spend_usd: 0.42,
              max_daily_cloud_usd: 10.0,
            }),
            { status: 200 },
          );
        }
        if (url.includes("/api/matches")) {
          return new Response(
            JSON.stringify({
              default_match_id: "mc9_semifinal",
              matches: MOCK_MATCHES,
            }),
            { status: 200 },
          );
        }
        if (url.includes("/api/system/status")) {
          return new Response(
            JSON.stringify({
              environment: "local",
              active_profile: "local",
              ollama_base_url: "http://127.0.0.1:11434",
              gemini_key_configured: true,
              masked_gemini_key: "AIza...7890",
              models: {
                tracker_model: "gemini-4-hydrogen",
                adjudicator_model: "gemini-4-neon",
                gemma_api_model: "gemma-4-26b-a4b-it",
                ollama_vision_model: "gemma4:e2b-it-q4_K_M",
              },
              providers: {
                cloud_gemini_configured: true,
                ollama_reachable: true,
                ollama_models: ["gemma4:e2b-it-q4_K_M"],
                recommended_local_model: "gemma4:e2b-it-q4_K_M",
              },
            }),
            { status: 200 },
          );
        }
        if (url.includes("/api/system/test-connection")) {
          return new Response(
            JSON.stringify({
              ok: true,
              target: "local",
              status: "200 OK",
              model: "gemma4:e2b-it-q4_K_M",
              latency_ms: 42.5,
              detail: "Connected to http://127.0.0.1:11434",
            }),
            { status: 200 },
          );
        }
        if (url.includes("/api/media/slowmo")) {
          return new Response(
            JSON.stringify({
              filename: "slowmo_real.mp4",
              output_path: "/tmp/slowmo_real.mp4",
              url: "/api/media/files/slowmo_real.mp4",
            }),
            { status: 200 },
          );
        }
        return new Response(JSON.stringify({ ok: true }), { status: 200 });
      }),
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("renders Kamon Lock mark, Gi-colored athlete attribution on call rows, and zero clutter", async () => {
    const { container } = render(<App />);

    await waitFor(() => {
      expect(screen.getByTestId("row-athlete-a")).toHaveTextContent("Kory Mathewson");
    });

    expect(screen.getByAltText("RollMetrics Kamon Lock")).toHaveAttribute("src", "/logo-mark.svg");
    expect(screen.getByText("RollMetrics")).toBeInTheDocument();
    // Division belt chip sits inside the scoreboard header; top-right shows Signed-In User Profile (#53)
    expect(screen.getByTestId("scoreboard-division")).toHaveTextContent("White Belt · Gi · Master");
    expect(screen.getByTestId("user-profile")).toHaveTextContent("KM");
    expect(screen.getByTestId("user-profile")).toHaveTextContent("Kory Mathewson");
    expect(screen.getByText("Michael Dube")).toBeInTheDocument();

    // Verify single unified transport (no duplicate native video controls) + numbered markers & tooltips (#61)
    const videoEl = container.querySelector("video");
    expect(videoEl).not.toBeNull();
    expect(videoEl?.hasAttribute("controls")).toBe(false);
    expect(screen.getByLabelText("Play video")).toBeInTheDocument();
    expect(screen.getByTestId("transport-time")).toHaveTextContent("00:00 / 03:45");

    const pin2 = screen.getByTestId("pin-2");
    expect(pin2).toHaveAttribute("aria-label", "Jump to #2 Dube +1 PEN");
    fireEvent.mouseEnter(pin2.parentElement as HTMLElement);
    expect(screen.getByRole("tooltip")).toHaveTextContent("#2 · Dube · +1 PEN · Cuff Grip");
    expect(screen.getByRole("tooltip").textContent).not.toMatch(/\b\d{2}:\d{2}\b/);
    expect(screen.getByTestId("call-row-2").className).toContain("cross-hover");
    fireEvent.mouseLeave(pin2.parentElement as HTMLElement);

    // 1-tap Prev/Next Key-Moment Stepper (#109)
    expect(screen.getByTestId("moment-stepper")).toHaveTextContent("3 / 3");
    fireEvent.click(screen.getByLabelText("Previous match moment"));
    expect(screen.getByTestId("moment-stepper")).toHaveTextContent("2 / 3");
    expect(screen.getByTestId("pen-b")).toHaveTextContent("1");

    // Unified Match Moments rail + inline collapsible Match Coaching Summary
    expect(screen.getByLabelText("Match Moments")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("moments-coach-toggle"));
    const coachPanel = screen.getByTestId("coach-summary-panel");
    expect(coachPanel).toHaveTextContent("Overview");
    expect(coachPanel).toHaveTextContent("Kory Mathewson");
    expect(coachPanel).toHaveTextContent("Michael Dube");
    expect(coachPanel).toHaveTextContent("CTA Curriculum ·");
    fireEvent.click(screen.getByTestId("moments-coach-toggle"));

    // Top-left logo -> Overview Home -> My Game Plan + My Matches + Film Study -> click match card (#117, #119)
    const matchSelect = screen.getByLabelText("Match Selector");
    expect(matchSelect.querySelector('optgroup[label="My Matches"]')).not.toBeNull();
    expect(matchSelect.querySelector('optgroup[label="Film Study"]')).not.toBeNull();

    fireEvent.click(screen.getByTestId("brand-home-btn"));
    expect(screen.getByTestId("overview-home")).toHaveTextContent("My Game Plan");
    expect(screen.getByTestId("overview-home")).not.toHaveTextContent("Local or Cloud");
    expect(screen.getByTestId("my-game-plan")).toHaveTextContent("2-0 (1 SUB, 1 DEC)");
    expect(screen.getByTestId("my-game-plan")).toHaveTextContent("0 points allowed");
    expect(screen.getByTestId("my-game-plan")).not.toHaveTextContent("Recurring Positions");
    expect(screen.getByTestId("athlete-profile-strip")).toBeInTheDocument();
    expect(screen.getByTestId("home-github-issues-link")).toHaveAttribute(
      "href",
      "https://github.com/rollmetrics/rollmetrics/issues",
    );
    expect(screen.getByTestId("home-my-matches")).toHaveTextContent("Mathewson vs. Dube");
    expect(screen.getByTestId("home-film-study")).toHaveTextContent("Terra vs. Oliveira");

    // Film Study athlete filter chip + Scouting Summary Card (#144)
    fireEvent.click(screen.getByTestId("film-study-filter-caio-terra"));
    await waitFor(() => {
      expect(screen.getByTestId("film-study-scouting-card")).toHaveTextContent("Caio Terra");
    });
    expect(screen.getByTestId("film-study-scouting-card")).toHaveTextContent("2-2 pts");
    fireEvent.click(screen.getByTestId("film-study-filter-all"));

    fireEvent.click(screen.getByTestId("home-match-mc9_semifinal"));
    expect(screen.queryByTestId("overview-home")).not.toBeInTheDocument();
    expect(screen.getByLabelText("Match Moments")).toBeInTheDocument();

    // Athlete attribution + Gi color dots on call rows (#48)
    expect(screen.getByTestId("actor-sf-1")).toHaveTextContent("Kory");
    expect(screen.getByTestId("actor-sf-1").querySelector(".gi-dot.white")).not.toBeNull();
    expect(screen.getByTestId("actor-sf-2")).toHaveTextContent("Dube");
    expect(screen.getByTestId("actor-sf-2").querySelector(".gi-dot.blue")).not.toBeNull();

    // Matching Gi color dots on scoreboard rows
    expect(screen.getByTestId("row-athlete-a").querySelector(".gi-dot.white")).not.toBeNull();
    expect(screen.getByTestId("row-athlete-b").querySelector(".gi-dot.blue")).not.toBeNull();

    const fullText = container.textContent || "";
    expect(fullText).not.toContain("rollmetrics.ai");
    expect(fullText).not.toContain("v0.1.0");
    expect(fullText).not.toContain("SHA256");
    expect(fullText).not.toContain("FFMPEG");
    expect(fullText).not.toContain("Confidence");
  });

  it("renders explicit Penalty on <Athlete> and Won by <Athlete> attribution on active calls", async () => {
    render(<App />);

    await waitFor(() => {
      expect(screen.getByTestId("row-athlete-a")).toHaveTextContent("Kory Mathewson");
    });

    expect(screen.getByTestId("match-clock")).toHaveTextContent("05:00");
    expect(screen.getByTestId("pen-b")).toHaveTextContent("0");

    // Click row #2 (+1 PEN · Dube · Cuff Grip) and verify 4-step Penalty Ladder + calibrated 04:09 clock (#63, #64, #75)
    fireEvent.click(screen.getByText("Cuff Grip"));
    expect(screen.getByTestId("match-clock")).toHaveTextContent("04:09");
    expect(screen.getByTestId("pen-b")).toHaveTextContent("1");
    expect(screen.getByText(/Penalty on Dube/i)).toBeInTheDocument();
    expect(screen.getByTestId("rule-cite-btn")).toHaveTextContent("IBJJF Art. 6.3.2 (F)");
    // Click IBJJF citation -> inline Rule Peek with rulebook excerpt + Official IBJJF Video link (#146)
    fireEvent.click(screen.getByTestId("rule-cite-btn"));
    expect(screen.getByTestId("rule-peek-box")).toHaveTextContent(
      "Placing fingers inside the opponent's sleeve or pant cuff is a technical penalty.",
    );
    expect(screen.getByTestId("rulebook-link")).toHaveTextContent("IBJJF Rulebook v6.1 ->");
    fireEvent.click(screen.getByTestId("rulebook-link"));
    await waitFor(() => {
      expect(screen.getByTestId("rulebook-page")).toBeInTheDocument();
      expect(screen.getByTestId("rulebook-article-highlighted")).toHaveTextContent("6.3.2");
    });
    fireEvent.click(screen.getByTestId("rulebook-back-btn"));
    await waitFor(() => {
      expect(screen.getByTestId("rule-peek-box")).toBeInTheDocument();
    });
    // Click Official IBJJF Video toggle -> embeds youtube-nocookie iframe inline (#215)
    fireEvent.click(screen.getByTestId("ibjjf-video-link"));
    expect(screen.getByTestId("ibjjf-rule-video").querySelector("iframe")).toHaveAttribute(
      "src",
      "https://www.youtube-nocookie.com/embed/RAwwWq7cI_o?rel=0&modestbranding=1",
    );
    expect(screen.getByTestId("penalty-ladder")).toHaveTextContent("1 Warning");
    expect(screen.getByTestId("penalty-ladder")).toHaveTextContent("2 +1 Adv to opp");
    expect(screen.getByTestId("penalty-ladder")).toHaveTextContent("Dube ● step 1");
    // Click Curriculum Peek toggle -> embeds academy reference inline (#215)
    expect(screen.getByTestId("cta-peek-btn")).toHaveTextContent(/Caio Terra/i);
    fireEvent.click(screen.getByTestId("cta-peek-btn"));
    expect(screen.getByTestId("cta-peek-box")).toBeInTheDocument();
    expect(screen.getByTestId("cta-peek-video")).toBeInTheDocument();

    // Click row #3 (SUBMISSION · Kory · Armbar) -> calibrated Smoothcomp finish clock 02:11
    fireEvent.click(screen.getByText("Armbar"));
    expect(screen.getByTestId("match-clock")).toHaveTextContent("02:11");
    expect(screen.getByText("SUB")).toBeInTheDocument();
    expect(screen.getByText(/Won by Kory/i)).toBeInTheDocument();
    expect(screen.getByTestId("rule-cite-btn")).toHaveTextContent("IBJJF Art. 2.2.1");
  });

  it("switches matches via dropdown, supports clicking anywhere on scrubber, and shows +2 PTS to Caio", async () => {
    render(<App />);

    await waitFor(() => {
      expect(screen.getByLabelText("Match Selector")).toBeInTheDocument();
    });

    // Click anywhere on scrubber track (at 50% width of 224.7s -> ~112s, after 01:07 penalty)
    const scrubber = screen.getByTestId("scrubber-track");
    const rail = scrubber.querySelector(".scrubber-rail") as HTMLElement;
    vi.spyOn(rail, "getBoundingClientRect").mockReturnValue({
      left: 0,
      width: 200,
      top: 0,
      height: 4,
      right: 200,
      bottom: 4,
      x: 0,
      y: 0,
      toJSON: () => ({}),
    });
    fireEvent.click(scrubber, { clientX: 100 });
    expect(screen.getByTestId("pen-b")).toHaveTextContent("1");

    // Switch via scalable Match Dropdown to Caio Terra vs. Vitor Oliveira
    fireEvent.change(screen.getByLabelText("Match Selector"), {
      target: { value: "caio_vitor_2014" },
    });
    expect(screen.getByTestId("scoreboard-division")).toHaveTextContent("Black Belt · Gi · Adult");
    expect(screen.getByTestId("actor-ct-2")).toHaveTextContent("Caio");
    expect(screen.getByTestId("actor-ct-2").querySelector(".gi-dot.blue")).not.toBeNull();

    fireEvent.click(screen.getByText("Single-Leg Sweep"));
    expect(screen.getByTestId("pts-a")).toHaveTextContent("2");
    expect(screen.getByText(/\+2 PTS to Caio/i)).toBeInTheDocument();
    expect(screen.getByTestId("rule-cite-btn")).toHaveTextContent("IBJJF Art. 4.6");
    expect(screen.getByText("3.0s CONTROL")).toBeInTheDocument();

    // Verify <3.0s hold downgrade bar (#63) + Bow-and-Arrow Choke attribution (#70)
    fireEvent.click(screen.getByText("Turtle Breakdown"));
    expect(screen.getByTestId("hold-downgrade-bar")).toHaveTextContent(
      "Hold 1.8s / 3.0s needed -> Advantage",
    );
    fireEvent.click(screen.getByText("Bow-and-Arrow Choke"));
    expect(screen.getByText(/Won by Vitor/i)).toBeInTheDocument();
    expect(screen.getByTestId("rule-cite-btn")).toHaveTextContent("IBJJF Art. 2.2.1");
  });

  it("opens inline GIF highlight, quiet + Analyze / Live drawer, /admin, and /ml-admin dashboards", async () => {
    render(<App />);

    await waitFor(() => {
      expect(screen.getByTestId("highlight-btn")).toBeInTheDocument();
    });
    expect(screen.queryByText("3-Panel")).not.toBeInTheDocument();
    expect(screen.getByTestId("cta-peek-btn")).toHaveTextContent("2:02");

    fireEvent.click(screen.getByTestId("highlight-btn"));
    await waitFor(() => {
      expect(screen.getByTestId("highlight-video")).toHaveAttribute(
        "src",
        "/api/media/files/slowmo_real.mp4",
      );
    });
    expect(screen.getByTestId("highlight-video")).toHaveAttribute("loop");
    expect(screen.getByTestId("highlight-download-link")).toHaveAttribute(
      "href",
      "/api/media/files/slowmo_real.mp4",
    );
    fireEvent.click(screen.getByTestId("highlight-btn"));

    fireEvent.click(screen.getByRole("button", { name: /Analyze or Live Camera/i }));
    expect(screen.getByTestId("upload-match-modal")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab", { name: "Live Camera" }));
    expect(screen.getByText("Use Live Camera")).toBeInTheDocument();
    expect(screen.getByText("Simulate Live from Match Video")).toBeInTheDocument();
    fireEvent.click(screen.getByText("Close"));

    // Open Profile, Connection & Model Config modal from top-right User Profile / Settings trigger (#55, #118, #232)
    fireEvent.click(screen.getByTestId("user-profile"));
    await waitFor(() => {
      expect(screen.getByText("Profile, Connection & Model Config")).toBeInTheDocument();
    });
    expect(screen.getByTestId("athlete-profile-section")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Athlete Academy"), { target: { value: "Plateau BJJ" } });
    fireEvent.change(screen.getByLabelText("Athlete Coach"), { target: { value: "Chris B" } });
    fireEvent.click(screen.getByTestId("save-athlete-profile-btn"));
    await waitFor(() => {
      expect(screen.getByTestId("profile-save-msg")).toHaveTextContent("Profile saved.");
    });
    expect(screen.getByText("Local (Ollama Gemma 4)")).toBeInTheDocument();
    expect(screen.getByText("Cloud (Gemini 4 Tiered)")).toBeInTheDocument();
    // Local selected -> only Local Connection visible (never stack both)
    expect(screen.getByText("Local Connection (Ollama)")).toBeInTheDocument();
    expect(screen.queryByText("Cloud Connection (Google Gemini API)")).not.toBeInTheDocument();
    fireEvent.click(screen.getByText("Test Local Connection"));
    await waitFor(() => {
      expect(screen.getByText(/42.5ms/i)).toBeInTheDocument();
    });

    // Offline Desktop Library inside Local settings (#179)
    expect(screen.getByTestId("offline-desktop-section")).toBeInTheDocument();
    expect(screen.getByTestId("offline-hardware-badge")).toHaveTextContent("Offline Ready");
    fireEvent.click(screen.getByTestId("scan-local-folder-btn"));
    await waitFor(() => {
      expect(screen.getByTestId("local-folder-list")).toHaveTextContent("local_roll.mp4");
    });
    fireEvent.change(screen.getByLabelText("Local Academy Notes Folder"), {
      target: { value: "/Users/test/Notes" },
    });
    fireEvent.click(screen.getByTestId("import-local-academy-btn"));
    await waitFor(() => {
      expect(screen.getByTestId("local-desktop-msg")).toHaveTextContent("Imported 3 note(s)");
    });
    // Switch to Cloud -> only Cloud Connection visible
    fireEvent.click(screen.getByText("Cloud (Gemini 4 Tiered)"));
    await waitFor(() => {
      expect(screen.getByText("Cloud Connection (Google Gemini API)")).toBeInTheDocument();
    });
    expect(screen.queryByText("Local Connection (Ollama)")).not.toBeInTheDocument();

    // Multi-Academy Reference Library inside Settings modal (#149)
    expect(screen.getByTestId("academy-library-section")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Academy Name"), {
      target: { value: "Alliance Montreal" },
    });
    fireEvent.click(screen.getByTestId("save-academy-btn"));
    await waitFor(() => {
      expect(screen.getByTestId("academy-msg")).toHaveTextContent("Alliance Montreal");
    });
    fireEvent.change(screen.getByLabelText("Reference Title"), {
      target: { value: "Knee Cut Pass" },
    });
    fireEvent.change(screen.getByLabelText("Reference Position"), {
      target: { value: "open_guard" },
    });
    fireEvent.change(screen.getByLabelText("Reference Video URL"), {
      target: { value: "https://example.com/knee-cut" },
    });
    fireEvent.change(screen.getByLabelText("Reference Coaching Notes"), {
      target: { value: "Pin the far hip first" },
    });
    fireEvent.click(screen.getByTestId("add-reference-btn"));
    await waitFor(() => {
      expect(screen.getByTestId("academy-ref-list")).toHaveTextContent("Knee Cut Pass");
    });
    fireEvent.click(screen.getByTestId("select-osjj-academy-btn"));
    await waitFor(() => {
      expect(screen.getByTestId("academy-msg")).toHaveTextContent("Open Source Jiu-Jitsu");
    });

    // App Admin (/admin) + ML Admin (/ml-admin) separation
    fireEvent.click(screen.getByTestId("open-admin-btn"));
    await waitFor(() => {
      expect(screen.getByTestId("admin-dashboard")).toBeInTheDocument();
    });
    expect(screen.getByTestId("admin-users-table")).toHaveTextContent("coach@example.com");
    expect(screen.getByTestId("admin-teams-table")).toBeInTheDocument();
    expect(screen.getByTestId("admin-curricula-table")).toBeInTheDocument();
    expect(screen.getByTestId("admin-rulesets-table")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("switch-ml-admin-btn"));
    await waitFor(() => {
      expect(screen.getByTestId("ml-admin-dashboard")).toBeInTheDocument();
    });
    expect(screen.getByTestId("admin-scoreboard-table")).toHaveTextContent(
      "gemini-4-hydrogen-agentic",
    );
    expect(screen.getByTestId("admin-kb-strip")).toHaveTextContent("231 docs");
    fireEvent.click(screen.getByTestId("admin-split-calibration"));
    expect(screen.getByTestId("admin-scoreboard-table")).toHaveTextContent("70%");
    fireEvent.click(screen.getByTestId("ml-admin-back-btn"));
    await waitFor(() => {
      expect(screen.queryByTestId("ml-admin-dashboard")).not.toBeInTheDocument();
    });

    // One-click Share Breakdown button copies link (#170 Workstream 2.3)
    fireEvent.click(screen.getByTestId("share-match-btn"));
    await waitFor(() => {
      expect(screen.getByTestId("share-match-btn")).toHaveTextContent("Link copied");
    });

    // Overview Home -> Upload Match modal (Camera Roll file + Google Drive import, #170 Workstream 2.2)
    fireEvent.click(screen.getByTestId("brand-home-btn"));
    fireEvent.click(screen.getByTestId("open-upload-modal-btn"));
    expect(screen.getByTestId("upload-match-modal")).toBeInTheDocument();
    const camFile = new File(["video-bytes"], "iphone_clip.mov", { type: "video/quicktime" });
    fireEvent.change(screen.getByTestId("camera-roll-file-input"), {
      target: { files: [camFile] },
    });
    await waitFor(() => {
      expect(screen.queryByTestId("upload-match-modal")).not.toBeInTheDocument();
    });
    fireEvent.click(screen.getByTestId("brand-home-btn"));
    fireEvent.click(screen.getByTestId("open-upload-modal-btn"));
    fireEvent.click(screen.getByRole("tab", { name: "Link (Drive / URL)" }));
    fireEvent.change(screen.getByLabelText("Google Drive Video URL"), {
      target: { value: "https://drive.google.com/file/d/1AbCdEfGhIjKlMnOpQrSt/view" },
    });
    fireEvent.click(screen.getByTestId("import-drive-btn"));
    await waitFor(() => {
      expect(screen.queryByTestId("upload-match-modal")).not.toBeInTheDocument();
    });
  });

  it("renders public /share/{token} breakdown without authentication and plays 0.4x highlight", async () => {
    window.history.pushState({}, "", "/share/tok_share_abc");
    render(<App />);

    await waitFor(() => {
      expect(screen.getByTestId("shared-breakdown-banner")).toHaveTextContent("Shared Breakdown");
    });
    expect(screen.getByTestId("row-athlete-a")).toHaveTextContent("Kory Mathewson");
    fireEvent.click(screen.getByTestId("highlight-btn"));
    await waitFor(() => {
      expect(screen.getByTestId("highlight-video")).toHaveAttribute(
        "src",
        "/api/share/tok_share_abc/media/slowmo_shared.mp4",
      );
    });
    fireEvent.click(screen.getByTestId("highlight-btn"));
    fireEvent.click(screen.getByTestId("share-open-app-btn"));
    window.history.pushState({}, "", "/");
  });

  it("exercises video transport controls, scrubber drag, error retry, drawer upload/URL/camera, gcs upload, and popstate", async () => {
    const origFetch = globalThis.fetch;
    const gcsPutSpy = vi.fn(async () => new Response("", { status: 200 }));
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.startsWith("https://storage.googleapis.com/")) {
          return gcsPutSpy();
        }
        if (url.includes("/api/uploads/init")) {
          return new Response(
            JSON.stringify({
              upload_id: "upl_gcs_1",
              upload_strategy: "gcs_signed_put",
              upload_url: "https://storage.googleapis.com/example-bucket/test.mov",
              method: "PUT",
              headers: { "Content-Type": "video/quicktime" },
              gcs_object: "uploads/test.mov",
            }),
            { status: 200 },
          );
        }
        if (url.includes("/api/uploads/complete")) {
          return new Response(
            JSON.stringify({
              match: {
                ...MOCK_MATCHES[0],
                id: "usr_gcs_1",
                pill: "GCS Phone Clip",
                title: "GCS Phone Clip",
                events: [
                  {
                    ...MOCK_MATCHES[0].events[0],
                    id: "pen2-each",
                    call: "PEN #2 EACH",
                    detail: "Double Stalling",
                    pen_a: 2,
                    pen_b: 2,
                  },
                  {
                    ...MOCK_MATCHES[0].events[1],
                    id: "dq-foul",
                    call: "DQ FOUL",
                    detail: "Illegal Leg Lock",
                    pen_b: 4,
                  },
                ],
              },
              snapshot: {},
            }),
            { status: 200 },
          );
        }
        return origFetch(input, init);
      }),
    );

    const stopTrack = vi.fn();
    Object.defineProperty(navigator, "mediaDevices", {
      configurable: true,
      value: {
        getUserMedia: vi.fn(async () => ({ getTracks: () => [{ stop: stopTrack }] })),
      },
    });

    const { container } = render(<App />);
    await waitFor(() => {
      expect(screen.getByTestId("row-athlete-a")).toBeInTheDocument();
    });

    const videoEl = container.querySelector("video") as HTMLVideoElement & {
      webkitEnterFullscreen?: () => void;
    };
    videoEl.play = vi.fn(() => Promise.reject(new Error("NotAllowedError")));
    videoEl.pause = vi.fn();
    videoEl.webkitEnterFullscreen = vi.fn();

    // Play (hits muted retry fallback), Space key, Mute/Unmute, Fullscreen, Waiting/CanPlay/Error+Retry, TimeUpdate
    fireEvent.click(screen.getByLabelText("Play video"));
    fireEvent.keyDown(window, { code: "Space" });
    fireEvent.click(screen.getByLabelText("Mute video"));
    fireEvent.click(screen.getByLabelText("Unmute video"));
    fireEvent.click(screen.getByLabelText("Toggle fullscreen"));
    expect(videoEl.webkitEnterFullscreen).toHaveBeenCalled();

    fireEvent.loadedMetadata(videoEl);
    fireEvent.waiting(videoEl);
    expect(screen.getByLabelText("Buffering video")).toBeInTheDocument();
    fireEvent.canPlay(videoEl);
    fireEvent.playing(videoEl);
    fireEvent.play(videoEl);
    fireEvent.pause(videoEl);
    fireEvent.timeUpdate(videoEl);
    fireEvent.error(videoEl);
    expect(screen.getByText("Video stream interrupted.")).toBeInTheDocument();
    fireEvent.click(screen.getByText("Retry"));

    // Scrubber pointer drag
    const scrubber = screen.getByTestId("scrubber-track");
    const rail = scrubber.querySelector(".scrubber-rail") as HTMLElement;
    vi.spyOn(rail, "getBoundingClientRect").mockReturnValue({
      left: 0,
      width: 200,
      top: 0,
      height: 4,
      right: 200,
      bottom: 4,
      x: 0,
      y: 0,
      toJSON: () => ({}),
    });
    const pd = new MouseEvent("pointerdown", { bubbles: true, clientX: 50 });
    const pm = new MouseEvent("pointermove", { bubbles: true, clientX: 120 });
    const pu = new MouseEvent("pointerup", { bubbles: true, clientX: 120 });
    fireEvent(scrubber, pd);
    fireEvent(scrubber, pm);
    fireEvent(scrubber, pu);

    // Unified + Add Match modal: Link tab URL analysis, bucket toggle, and Live Camera
    fireEvent.click(screen.getByRole("button", { name: /Analyze or Live Camera/i }));
    fireEvent.click(screen.getByRole("button", { name: "Film Study" }));
    fireEvent.click(screen.getByRole("button", { name: "My Matches" }));
    fireEvent.click(screen.getByRole("tab", { name: "Link (Drive / URL)" }));
    fireEvent.change(screen.getByLabelText("Google Drive Video URL"), {
      target: { value: "https://youtu.be/test123" },
    });
    fireEvent.click(screen.getByTestId("import-drive-btn"));
    await waitFor(() => {
      expect(screen.queryByTestId("upload-match-modal")).not.toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole("button", { name: /Analyze or Live Camera/i }));
    fireEvent.click(screen.getByRole("tab", { name: "Live Camera" }));
    fireEvent.click(screen.getByText("Use Live Camera"));
    await waitFor(() => {
      expect(screen.getByTestId("live-sim-badge")).toHaveTextContent("LIVE CAM");
    });
    fireEvent.click(screen.getByTestId("stop-live-btn"));

    // GCS Signed PUT Upload + PEN #2 EACH + DQ FOUL attribution
    fireEvent.click(screen.getByTestId("brand-home-btn"));
    fireEvent.click(screen.getByTestId("open-upload-modal-btn"));
    fireEvent.change(screen.getByLabelText("Match Title"), { target: { value: "GCS Phone Clip" } });
    fireEvent.change(screen.getByLabelText("Uniform"), { target: { value: "nogi" } });
    fireEvent.change(screen.getByLabelText("Belt Rank"), { target: { value: "blue" } });
    fireEvent.change(screen.getByLabelText("Athlete A Name"), { target: { value: "Kory" } });
    fireEvent.change(screen.getByLabelText("Athlete A Gi"), { target: { value: "black" } });
    fireEvent.change(screen.getByLabelText("Athlete B Name"), { target: { value: "Rival" } });
    fireEvent.change(screen.getByLabelText("Athlete B Gi"), { target: { value: "white" } });
    fireEvent.change(screen.getByTestId("camera-roll-file-input"), {
      target: { files: [new File(["mov"], "phone.mov", { type: "video/quicktime" })] },
    });
    await waitFor(() => {
      expect(gcsPutSpy).toHaveBeenCalledTimes(1);
    });
    await waitFor(() => {
      expect(screen.getByText("Double Stalling")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByText("Double Stalling"));
    expect(screen.getByText(/2nd penalty each/i)).toBeInTheDocument();
    fireEvent.click(screen.getByText("Illegal Leg Lock"));
    expect(screen.getByText(/Disqualification on/i)).toBeInTheDocument();

    // Settings -> Offline Desktop -> Analyze Offline + Save Settings
    fireEvent.click(screen.getByTestId("user-profile"));
    await waitFor(() => {
      expect(screen.getByTestId("scan-local-folder-btn")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByTestId("scan-local-folder-btn"));
    await waitFor(() => {
      expect(screen.getByTestId("analyze-offline-btn")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByTestId("analyze-offline-btn"));
    await waitFor(() => {
      expect(screen.queryByTestId("offline-desktop-section")).not.toBeInTheDocument();
    });

    // Browser popstate across /admin, /privacy, /terms, /
    for (const p of ["/admin", "/privacy", "/terms", "/"]) {
      window.history.pushState({}, "", p);
      fireEvent(window, new PopStateEvent("popstate"));
    }
  });

  it("enforces Cloud Google Sign-In gate, renders signed-in avatar + name, and supports Sign Out", async () => {
    const gisHolder: { cb?: (resp: { credential?: string }) => void } = {};
    const disableAutoSelect = vi.fn();
    const initializeSpy = vi.fn(
      (cfg: { client_id: string; callback: (resp: { credential?: string }) => void }) => {
        gisHolder.cb = cfg.callback;
      },
    );
    const renderButtonSpy = vi.fn((el: HTMLElement) => {
      const btn = document.createElement("button");
      btn.textContent = "Sign in with Google";
      el.appendChild(btn);
    });
    window.google = {
      accounts: {
        id: {
          initialize: initializeSpy,
          renderButton: renderButtonSpy,
          disableAutoSelect,
        },
      },
    };

    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        const authHeader = new Headers(init?.headers || {}).get("Authorization") || "";
        if (url.includes("/api/auth/config")) {
          return new Response(
            JSON.stringify({
              environment: "cloud",
              google_client_id: "test-client.apps.googleusercontent.com",
              allow_local_dev_bypass: false,
              auth_required: true,
              public_url: "https://example.com",
              default_profile: "cloud",
            }),
            { status: 200 },
          );
        }
        if (authHeader === "Bearer valid-kory-jwt" && url.includes("/api/auth/me")) {
          return new Response(
            JSON.stringify({
              email: "coach@example.com",
              name: "Kory Mathewson",
              picture: "https://lh3.googleusercontent.com/a/kory-avatar",
              auth_mode: "google_id_token",
            }),
            { status: 200 },
          );
        }
        if (authHeader === "Bearer valid-kory-jwt" && url.includes("/api/matches")) {
          return new Response(
            JSON.stringify({
              default_match_id: "mc9_semifinal",
              matches: MOCK_MATCHES,
            }),
            { status: 200 },
          );
        }
        if (authHeader === "Bearer outsider-jwt" && url.includes("/api/auth/me")) {
          return new Response(
            JSON.stringify({
              detail: "Account 'outsider@gmail.com' is outside the authorized RollMetrics allowlist.",
            }),
            { status: 403 },
          );
        }
        return new Response(JSON.stringify({ detail: "Unauthorized" }), { status: 401 });
      }),
    );

    render(<App />);

    // 1) Public Cloud Landing Page + Google Sign-In
    await waitFor(() => {
      expect(screen.getByTestId("cloud-signin-gate")).toBeInTheDocument();
    });
    expect(screen.getByAltText("RollMetrics mark")).toHaveAttribute("src", "/logo-mark.svg");
    expect(screen.getAllByText("RollMetrics").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText("Sign in with Google")).toBeInTheDocument();
    expect(initializeSpy).toHaveBeenCalledTimes(1);
    expect(screen.queryByPlaceholderText("Google ID token")).not.toBeInTheDocument();
    expect(screen.queryByText(/authorized Google account/i)).not.toBeInTheDocument();
    expect(screen.queryByText("Match Calls")).not.toBeInTheDocument();

    // Concise landing page with GitHub link and Sebastian demo login
    expect(screen.getByTestId("landing-github-cta")).toHaveAttribute(
      "href",
      "https://github.com/rollmetrics/rollmetrics",
    );
    expect(screen.getByTestId("demo-sebastian-btn")).toBeInTheDocument();

    // Public /privacy and /terms accessible without sign-in (#147, #165)
    fireEvent.click(screen.getByRole("button", { name: "Privacy" }));
    const privacyText = screen.getByTestId("privacy-page").textContent || "";
    expect(privacyText).toContain("Privacy Policy");
    expect(privacyText).toContain("we never sell your data");
    for (const forbidden of ["coach@example.com", "Caio Terra", "CTA", "Ollama", "Google", "Gemini"]) {
      expect(privacyText).not.toContain(forbidden);
    }
    fireEvent.click(screen.getByRole("button", { name: "Terms" }));
    const termsText = screen.getByTestId("terms-page").textContent || "";
    expect(termsText).toContain("Terms of Service");
    expect(termsText).toContain("provided as-is without warranties");
    for (const forbidden of ["coach@example.com", "Caio Terra", "CTA", "Ollama", "Google", "Gemini"]) {
      expect(termsText).not.toContain(forbidden);
    }
    fireEvent.click(screen.getByTestId("legal-back-btn"));
    expect(screen.getByTestId("cloud-signin-gate")).toBeInTheDocument();

    // Non-allowlisted 403 account shows exact quiet 1-liner: "This account doesn't have access."
    gisHolder.cb?.({ credential: "outsider-jwt" });
    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent("This account doesn't have access.");
    });

    // 2) Complete Google Sign-In -> signed-in avatar + name top-right + Sign Out button
    expect(gisHolder.cb).toBeDefined();
    gisHolder.cb?.({ credential: "valid-kory-jwt" });

    await waitFor(() => {
      expect(screen.getByLabelText("Match Moments")).toBeInTheDocument();
    });
    const profileBtn = screen.getByTestId("user-profile");
    expect(profileBtn).toHaveTextContent("Kory Mathewson");
    expect(profileBtn.querySelector("img.user-avatar")).toHaveAttribute(
      "src",
      "https://lh3.googleusercontent.com/a/kory-avatar",
    );

    // 3) Click Sign Out -> clears session, calls disableAutoSelect, returns cleanly to Cloud Sign-In gate
    fireEvent.click(screen.getByTestId("topbar-signout"));
    expect(disableAutoSelect).toHaveBeenCalledTimes(1);
    await waitFor(() => {
      expect(screen.getByTestId("cloud-signin-gate")).toBeInTheDocument();
    });
    expect(screen.queryByLabelText("Match Calls")).not.toBeInTheDocument();
    expect(screen.getByText("Sign in with Google")).toBeInTheDocument();
    delete window.google;
  });

  it("simulates live camera from match video over /ws/live with canvas capture and HUD updates", async () => {
    const sentPayloads: Array<Record<string, unknown>> = [];
    const wsHolder: { current?: MockWebSocket } = {};

    class MockWebSocket {
      static OPEN = 1;
      url: string;
      readyState = 1;
      onopen: (() => void) | null = null;
      onmessage: ((e: { data: string }) => void) | null = null;
      onclose: (() => void) | null = null;
      close = vi.fn(() => {
        this.readyState = 3;
        this.onclose?.();
      });
      constructor(url: string) {
        this.url = url;
        wsHolder.current = this;
        window.setTimeout(() => this.onopen?.(), 0);
      }
      send(raw: string) {
        sentPayloads.push(JSON.parse(raw) as Record<string, unknown>);
      }
    }

    vi.stubGlobal("WebSocket", MockWebSocket);
    const drawImageSpy = vi.fn();
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue({
      drawImage: drawImageSpy,
    } as unknown as CanvasRenderingContext2D);
    vi.spyOn(HTMLCanvasElement.prototype, "toDataURL").mockReturnValue(
      "data:image/jpeg;base64,QUJDRA==",
    );
    vi.spyOn(HTMLMediaElement.prototype, "play").mockResolvedValue();
    vi.spyOn(HTMLMediaElement.prototype, "pause").mockImplementation(() => {});

    const { container } = render(<App />);
    await waitFor(() => {
      expect(screen.getByTestId("row-athlete-a")).toHaveTextContent("Kory Mathewson");
    });

    // Verify iOS poster + playsInline + webkit-playsinline + center Play overlay (no crossOrigin breaking iOS Range) (#108, #114)
    const videoEl = container.querySelector("video") as HTMLVideoElement & {
      webkitEnterFullscreen?: () => void;
    };
    expect(videoEl).toHaveAttribute("poster", "/api/matches/mc9_semifinal/poster");
    expect(videoEl).not.toHaveAttribute("crossorigin");
    expect(videoEl).toHaveAttribute("webkit-playsinline", "true");
    expect(screen.getByLabelText("Start playback")).toBeInTheDocument();

    // Verify iOS NotAllowedError -> muted retry fallback & webkitEnterFullscreen fallback (#114)
    const playMock = vi
      .spyOn(HTMLMediaElement.prototype, "play")
      .mockRejectedValueOnce(new DOMException("NotAllowedError", "NotAllowedError"))
      .mockResolvedValue();
    fireEvent.click(screen.getByLabelText("Play video"));
    await waitFor(() => {
      expect(playMock).toHaveBeenCalledTimes(2);
    });
    expect(videoEl.muted).toBe(true);

    const webkitFsSpy = vi.fn();
    videoEl.webkitEnterFullscreen = webkitFsSpy;
    fireEvent.click(screen.getByLabelText("Toggle fullscreen"));
    expect(webkitFsSpy).toHaveBeenCalledTimes(1);
    delete videoEl.webkitEnterFullscreen;

    // Open unified + Add Match modal -> Live Camera tab -> launch Simulate Live from Match Video (#106, #232)
    fireEvent.click(screen.getByRole("button", { name: /Analyze or Live Camera/i }));
    fireEvent.click(screen.getByRole("tab", { name: "Live Camera" }));
    fireEvent.click(screen.getByText("Simulate Live from Match Video"));

    await waitFor(() => {
      expect(screen.getByTestId("live-sim-badge")).toHaveTextContent("LIVE SIM");
    });
    expect(wsHolder.current?.url).toContain("/ws/live");

    // Wait for init + first 1.5s interval canvas frame capture
    await waitFor(
      () => {
        expect(sentPayloads.some((p) => p.type === "init")).toBe(true);
        expect(sentPayloads.some((p) => p.type === "frame" && p.image_b64 === "QUJDRA==")).toBe(true);
      },
      { timeout: 3000 },
    );
    expect(drawImageSpy).toHaveBeenCalled();

    // Stream back a live WebSocket HUD snapshot with +4 Mount, 3.0s CONTROL, and latency telemetry (#107)
    wsHolder.current?.onmessage?.({
      data: JSON.stringify({
        type: "hud",
        latency_ms: 2400,
        model_id: "gemma4:e2b-it-q4_K_M",
        active_stabilization: { kind: "mount", held_sec: 1.8 },
        scoreboard: {
          current_position: "Mount",
          athlete_a: { points: 4, advantages: 0, penalties: 0 },
          athlete_b: { points: 0, advantages: 0, penalties: 0 },
          ended: false,
          timeline: [
            {
              event_id: "live-1",
              timestamp_sec: 32.0,
              match_clock_str: "04:28",
              actor: "A",
              raw_kind: "mount",
              awarded_points: 4,
              position_after: "Mount",
              rule_article: "IBJJF Art. 4.4",
            },
          ],
        },
      }),
    });

    await waitFor(() => {
      expect(screen.getByTestId("pts-a")).toHaveTextContent("4");
    });
    expect(screen.getByText("3.0s CONTROL · 1.8s")).toBeInTheDocument();
    expect(screen.getByTestId("live-telemetry-strip")).toHaveTextContent(
      "Causal · Last inference: 2.4s · gemma4:e2b-it-q4_K_M",
    );
    expect(screen.getByLabelText("Match Moments")).toHaveTextContent("Mount");

    // Stop Live closes WebSocket cleanly and restores normal mode
    fireEvent.click(screen.getByTestId("stop-live-btn"));
    expect(wsHolder.current?.close).toHaveBeenCalled();
    expect(screen.queryByTestId("live-sim-badge")).not.toBeInTheDocument();
  });

  it("parses and embeds YouTube, hosted MP4, and doc links on-page across Coach Tab and Overview Home (#215)", async () => {
    expect(parseTimestampParam("90")).toBe(90);
    expect(parseTimestampParam("1m30s")).toBe(90);
    expect(parseTimestampParam("1h2m3s")).toBe(3723);
    expect(parseTimestampParam("bad")).toBe(0);
    expect(parseTimestampParam(null)).toBe(0);

    expect(parseReferenceMedia("https://youtu.be/dQw4w9WgXcQ?t=45")).toMatchObject({
      kind: "youtube",
      embedUrl: "https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ?rel=0&modestbranding=1&start=45",
      startSec: 45,
    });
    expect(parseReferenceMedia("https://www.youtube.com/shorts/abcdefghijk")).toMatchObject({
      kind: "youtube",
      embedUrl: "https://www.youtube-nocookie.com/embed/abcdefghijk?rel=0&modestbranding=1",
    });
    expect(parseReferenceMedia("/api/academies/me/video/clip.mp4", 12)).toMatchObject({
      kind: "video",
      videoUrl: "/api/academies/me/video/clip.mp4",
      startSec: 12,
    });
    expect(parseReferenceMedia("https://ibjjf.com/books-videos")).toMatchObject({
      kind: "link",
      originalUrl: "https://ibjjf.com/books-videos",
    });
    expect(parseReferenceMedia("")).toMatchObject({ kind: "link", originalUrl: "" });

    // Direct HTML5 video embed seeks to startSec on loadedmetadata
    const { unmount } = render(
      <EmbeddedReferenceVideo
        url="/api/academies/me/clip.mp4"
        title="Academy Clip"
        startSec={15}
        resolveVideoUrl={(u) => `${u}?token=abc`}
        testId="direct-mp4-embed"
      />,
    );
    const vidEl = screen.getByTestId("direct-mp4-embed").querySelector("video") as HTMLVideoElement;
    expect(vidEl).toHaveAttribute("src", "/api/academies/me/clip.mp4?token=abc");
    fireEvent.loadedMetadata(vidEl);
    expect(vidEl.currentTime).toBe(15);
    unmount();

    // Non-video link renders fallback anchor
    const { unmount: unmountLink } = render(
      <EmbeddedReferenceVideo
        url="https://ibjjf.com/books-videos"
        title="IBJJF Docs"
        testId="doc-link-embed"
      />,
    );
    expect(screen.getByTestId("doc-link-embed")).toHaveTextContent("IBJJF Docs ->");
    unmountLink();

    // Verify Coach Tab inline embed + Overview Home Gameplan & Film Study Scouting modals
    render(<App />);
    await waitFor(() => {
      expect(screen.getByTestId("row-athlete-a")).toHaveTextContent("Kory Mathewson");
    });

    // Inline Match Coaching Summary video expand
    fireEvent.click(screen.getByTestId("moments-coach-toggle"));
    const coachBtn = screen.getByTestId("coach-video-btn-kory-mathewson-0");
    fireEvent.click(coachBtn);
    expect(screen.getByTestId("coach-video-embed-kory-mathewson-0")).toBeInTheDocument();
    fireEvent.click(coachBtn);
    expect(screen.queryByTestId("coach-video-embed-kory-mathewson-0")).not.toBeInTheDocument();

    // Overview Home -> Personal Gameplan Priority opens shared reference-video-modal
    fireEvent.click(screen.getByTestId("brand-home-btn"));
    await waitFor(() => {
      expect(screen.getByTestId("overview-home")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByTestId("gameplan-video-btn-0"));
    expect(screen.getByTestId("reference-video-modal")).toBeInTheDocument();
    expect(screen.getByTestId("reference-modal-video")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("close-reference-modal-btn"));
    expect(screen.queryByTestId("reference-video-modal")).not.toBeInTheDocument();

    // Film Study Scouting Card -> opens shared reference-video-modal
    fireEvent.click(screen.getByTestId("film-study-filter-caio-terra"));
    await waitFor(() => {
      expect(screen.getByTestId("film-study-scouting-card")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByTestId("scouting-video-btn-0"));
    expect(screen.getByTestId("reference-video-modal")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("close-reference-modal-btn"));
    expect(screen.queryByTestId("reference-video-modal")).not.toBeInTheDocument();
  });

  it("exercises v0.3.0 inline moment editing, share revoke, upload ruleset/trim/tap-to-identify, auto-tag, export zip, and team coach roster", async () => {
    render(<App />);
    await waitFor(() => {
      expect(screen.getByTestId("row-athlete-a")).toHaveTextContent("Kory Mathewson");
    });

    // 1. Share + Revoke Share link
    fireEvent.click(screen.getByTestId("share-match-btn"));
    await waitFor(() => {
      expect(screen.getByTestId("revoke-share-btn")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByTestId("revoke-share-btn"));
    await waitFor(() => {
      expect(screen.queryByTestId("revoke-share-btn")).not.toBeInTheDocument();
    });

    // 2. Inline Human-in-the-Loop Moment Editing (+ Moment -> Edit -> Save -> Delete)
    fireEvent.click(screen.getByTestId("add-moment-btn"));
    await waitFor(() => {
      expect(screen.getByTestId("moment-editor-form")).toBeInTheDocument();
    });
    fireEvent.change(screen.getByLabelText("Edit Moment Actor"), { target: { value: "B" } });
    fireEvent.change(screen.getByLabelText("Edit Moment Call"), { target: { value: "+3 PTS" } });
    fireEvent.change(screen.getByLabelText("Edit Moment Detail"), { target: { value: "Guard Pass" } });
    fireEvent.change(screen.getByLabelText("Edit Coach Note"), { target: { value: "Pin far hip" } });
    fireEvent.click(screen.getByTestId("save-moment-btn"));
    await waitFor(() => {
      expect(screen.queryByTestId("moment-editor-form")).not.toBeInTheDocument();
    });
    fireEvent.click(screen.getByTestId("edit-moment-btn"));
    fireEvent.click(screen.getByTestId("delete-moment-btn"));
    await waitFor(() => {
      expect(screen.queryByTestId("moment-editor-form")).not.toBeInTheDocument();
    });

    // 3. Settings modal -> Auto-Tag Academy Reference & Export Data (.zip) link
    fireEvent.click(screen.getByTestId("user-profile"));
    await waitFor(() => {
      expect(screen.getByTestId("export-data-zip-link")).toHaveAttribute("href", "/api/me/export.zip");
    });
    fireEvent.change(screen.getByLabelText("Reference Video URL"), {
      target: { value: "https://youtu.be/dQw4w9WgXcQ" },
    });
    fireEvent.click(screen.getByTestId("auto-tag-ref-btn"));
    await waitFor(() => {
      expect(screen.getByTestId("academy-msg")).toHaveTextContent("Auto-tagged: guard_passing");
    });
    fireEvent.click(screen.getByRole("button", { name: "Close" }));

    // 4. Overview Home -> Longitudinal Progress Strip + Team Coach Roster + Upload Modal (Ruleset + Trim + Tap-to-Identify)
    fireEvent.click(screen.getByTestId("brand-home-btn"));
    await waitFor(() => {
      expect(screen.getByTestId("longitudinal-progress-strip")).toBeInTheDocument();
      expect(screen.getByTestId("team-coach-roster")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByTestId("toggle-review-mc9_semifinal"));
    await waitFor(() => {
      expect(screen.getByTestId("toggle-review-mc9_semifinal")).toHaveTextContent("Mark Needs Review");
    });
    fireEvent.click(screen.getByTestId("pin-drill-mc9_semifinal"));
    await waitFor(() => {
      expect(screen.getByTestId("pin-drill-mc9_semifinal")).toHaveTextContent("Pin Drill (1)");
    });

    fireEvent.click(screen.getByTestId("open-upload-modal-btn"));
    fireEvent.change(screen.getByTestId("upload-ruleset-select"), { target: { value: "adcc" } });
    fireEvent.change(screen.getByLabelText("Trim Start Seconds"), { target: { value: "5" } });
    fireEvent.change(screen.getByLabelText("Trim End Seconds"), { target: { value: "120" } });
    const tapCanvas = screen.getByTestId("tap-to-identify-canvas");
    fireEvent.click(tapCanvas, { clientX: 40, clientY: 30 });
    expect(screen.getByTestId("tap-pin-a")).toBeInTheDocument();
    fireEvent.click(tapCanvas, { clientX: 80, clientY: 30 });
    expect(screen.getByTestId("tap-pin-b")).toBeInTheDocument();
    // Third click resets A and clears B
    fireEvent.click(tapCanvas, { clientX: 50, clientY: 40 });
    expect(screen.queryByTestId("tap-pin-b")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Reset Pins" }));
    expect(screen.queryByTestId("tap-pin-a")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Close" }));

    // Resizable right-hand Moments panel handle on desktop (#233)
    fireEvent.click(screen.getByTestId("home-match-mc9_semifinal"));
    await waitFor(() => {
      expect(screen.getByTestId("rail-resize-handle")).toBeInTheDocument();
    });
    const resizeHandle = screen.getByTestId("rail-resize-handle");
    fireEvent.pointerDown(resizeHandle, { clientX: 800 });
    fireEvent(window, new MouseEvent("pointermove", { clientX: 740 }));
    fireEvent(window, new MouseEvent("pointerup"));
  }, 15000);
});
