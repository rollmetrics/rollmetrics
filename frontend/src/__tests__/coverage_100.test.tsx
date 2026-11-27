import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { setStoredToken } from "../api";
import { App } from "../App";
import { EmbeddedReferenceVideo, parseReferenceMedia } from "../components/EmbeddedReferenceVideo";
import type { ShowcaseMatch } from "../types";

const CUSTOM_MATCH: ShowcaseMatch = {
  id: "custom_100",
  pill: "Custom vs. Opponent",
  title: "Custom Match · Final",
  belt: "blue",
  belt_label: "Blue Belt · Gi · Adult",
  bucket: "my_matches",
  owner_email: "coach@example.com",
  regulation_sec: 300,
  video_duration_sec: 120,
  video_start_sec: 5,
  video_end_sec: 115,
  athlete_a: { name: "Alex", belt: "blue", gi: "white" },
  athlete_b: { name: "Sam", belt: "blue", gi: "blue" },
  winner: "A",
  win_label: "PTS",
  events: [
    {
      id: "c-1",
      video_sec: 15,
      time_str: "00:15",
      clock_str: "04:45",
      actor: "A",
      call: "PEN #1 EACH",
      detail: "Double guard pull",
      rule: "IBJJF Art. 6.4",
      cta_url: "https://youtu.be/dQw4w9WgXcQ",
      pts_a: 0,
      adv_a: 0,
      pen_a: 1,
      pts_b: 0,
      adv_b: 0,
      pen_b: 1,
    },
    {
      id: "c-2",
      video_sec: 118,
      time_str: "01:58",
      clock_str: "03:02",
      actor: "A",
      call: "+1 PEN",
      detail: "Stalling",
      rule: "IBJJF Art. 6.4.1",
      cta_url: "https://youtu.be/dQw4w9WgXcQ",
      pts_a: 0,
      adv_a: 0,
      pen_a: 2,
      pts_b: 0,
      adv_b: 1,
      pen_b: 1,
    },
  ],
};

describe("Wave 3 100% Frontend Coverage", () => {
  beforeEach(() => {
    setStoredToken("");
    window.history.pushState({}, "", "/");
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("covers remaining branches in EmbeddedReferenceVideo (100%)", () => {
    // 1. EmbeddedReferenceVideo URL parse catch (lines 34-35)
    const origURL = globalThis.URL;
    vi.stubGlobal("URL", function BrokenURL() {
      throw new TypeError("bad url");
    });
    expect(parseReferenceMedia("https://example.com/a")).toMatchObject({ kind: "link" });
    vi.stubGlobal("URL", origURL);

    // 2. EmbeddedReferenceVideo currentTime throw catch (line 114)
    const { container } = render(
      <EmbeddedReferenceVideo
        url="/api/academies/me/clip.mp4"
        title="Clip"
        startSec={10}
        testId="throw-seek-vid"
      />,
    );
    const vid = container.querySelector("video") as HTMLVideoElement;
    Object.defineProperty(vid, "currentTime", {
      set() {
        throw new Error("unloaded");
      },
      configurable: true,
    });
    fireEvent.loadedMetadata(vid);
  });

  it("covers auth error fallback, GIS polling, demo login, share errors, and cloud non-admin settings", async () => {
    // 1. Auth config failure fallback (lines 629-637)
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("offline")));
    const { unmount } = render(<App />);
    await waitFor(() => {
      expect(screen.getByTestId("row-athlete-a")).toBeInTheDocument();
    });
    unmount();

    // 2. Shared match load error + Open RollMetrics button (lines 724, 1910-1919)
    window.history.pushState({}, "", "/share/bad_tok");
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes("/api/share/bad_tok")) {
          return new Response(JSON.stringify({ detail: "Not found" }), { status: 404 });
        }
        return new Response(JSON.stringify({ environment: "local", auth_required: false }), { status: 200 });
      }),
    );
    const { unmount: unmountShareErr } = render(<App />);
    await waitFor(() => {
      expect(screen.getByText("Open RollMetrics")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByText("Open RollMetrics"));
    unmountShareErr();

    // 3. Shared match success + Open App topbar button (lines 2054-2055)
    window.history.pushState({}, "", "/share/ok_tok");
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes("/api/share/ok_tok")) {
          return new Response(
            JSON.stringify({ token: "ok_tok", academy_name: "Academy", match: CUSTOM_MATCH }),
            { status: 200 },
          );
        }
        return new Response(JSON.stringify({ environment: "local", auth_required: false }), { status: 200 });
      }),
    );
    const { unmount: unmountShareOk } = render(<App />);
    await waitFor(() => {
      expect(screen.getByTestId("share-open-app-btn")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByTestId("share-open-app-btn"));
    unmountShareOk();

    // 4. Cloud sign-in gate: GIS polling timer + demo login error + demo login success + cloud non-admin Settings
    window.history.pushState({}, "", "/");
    let demoFail = true;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes("/api/auth/config")) {
          return new Response(
            JSON.stringify({
              environment: "cloud",
              google_client_id: "cid-123",
              allow_local_dev_bypass: false,
              auth_required: true,
              public_url: "https://example.com",
              default_profile: "cloud",
            }),
            { status: 200 },
          );
        }
        if (url.includes("/api/auth/demo-login")) {
          if (demoFail) {
            demoFail = false;
            return new Response(JSON.stringify({ detail: "Demo temporarily busy" }), { status: 503 });
          }
          return new Response(
            JSON.stringify({
              token: "demo-tok",
              user: {
                email: "demo@example.com",
                name: "Demo Athlete",
                picture: "https://example.com/pic.png",
                auth_mode: "demo_session",
                role: "demo",
                is_admin: false,
              },
            }),
            { status: 200 },
          );
        }
        if (url.includes("/api/auth/me")) {
          return new Response(
            JSON.stringify({
              email: "demo@example.com",
              name: "Demo Athlete",
              picture: "https://example.com/pic.png",
              auth_mode: "demo_session",
              role: "demo",
              is_admin: false,
            }),
            { status: 200 },
          );
        }
        if (url.includes("/api/matches")) {
          return new Response(JSON.stringify({ default_match_id: "custom_100", matches: [CUSTOM_MATCH] }), {
            status: 200,
          });
        }
        return new Response(JSON.stringify({}), { status: 200 });
      }),
    );

    const { container: cloudContainer, unmount: unmountCloud } = render(<App />);
    await waitFor(() => {
      expect(screen.getByTestId("demo-sebastian-btn")).toBeInTheDocument();
    });
    // Interactive landing page video preview (#288): static list of all 4 moments + two-way sync with timeline diamonds
    expect(screen.queryByTestId("landing-slowmo-toggle")).not.toBeInTheDocument();
    expect(screen.getByTestId("landing-moment-0")).toBeInTheDocument();
    expect(screen.getByTestId("landing-moment-1")).toBeInTheDocument();
    expect(screen.getByTestId("landing-moment-2")).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByTestId("landing-moment-3")).toBeInTheDocument();
    expect(screen.getByTestId("landing-score-a")).toHaveTextContent("422");
    expect(screen.getByTestId("landing-score-b")).toHaveTextContent("222");

    fireEvent.click(screen.getByTestId("landing-pin-0"));
    expect(screen.getByTestId("landing-score-clock")).toHaveTextContent("02:34");
    expect(screen.getByTestId("landing-moment-0")).toHaveAttribute("aria-pressed", "true");

    fireEvent.click(screen.getByTestId("landing-pin-1"));
    expect(screen.getByTestId("landing-score-clock")).toHaveTextContent("02:31");
    expect(screen.getByTestId("landing-moment-1")).toHaveAttribute("aria-pressed", "true");

    fireEvent.click(screen.getByTestId("landing-pin-2"));
    expect(screen.getByTestId("landing-score-clock")).toHaveTextContent("02:28");
    expect(screen.getByTestId("landing-moment-2")).toHaveAttribute("aria-pressed", "true");

    fireEvent.click(screen.getByTestId("landing-pin-3"));
    expect(screen.getByTestId("landing-score-clock")).toHaveTextContent("02:25");
    expect(screen.getByTestId("landing-moment-3")).toHaveAttribute("aria-pressed", "true");

    // Clicking a moment row in the static list syncs video + diamond
    fireEvent.click(screen.getByTestId("landing-moment-0"));
    expect(screen.getByTestId("landing-pin-0")).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByTestId("landing-score-clock")).toHaveTextContent("02:34");

    const landingVid = cloudContainer.querySelector("video") as HTMLVideoElement;
    Object.defineProperty(landingVid, "currentTime", { value: 6.5, writable: true, configurable: true });
    fireEvent.timeUpdate(landingVid);
    expect(screen.getByTestId("landing-score-clock")).toHaveTextContent("02:28");
    expect(screen.getByTestId("landing-moment-2")).toHaveAttribute("aria-pressed", "true");
    // Trigger GIS polling interval tick
    await new Promise((r) => setTimeout(r, 260));
    // Demo login error path -> shows generic sign-in error
    fireEvent.click(screen.getByTestId("demo-sebastian-btn"));
    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent("Sign-in failed. Please try again.");
    });
    // Demo login success -> lands on home with user picture avatar
    fireEvent.click(screen.getByTestId("demo-sebastian-btn"));
    await waitFor(() => {
      expect(screen.getByTestId("overview-home")).toBeInTheDocument();
    });
    // Open Settings as non-admin in Cloud mode -> hides infra config, shows Athlete Profile & Settings
    fireEvent.click(screen.getByTestId("user-profile"));
    await waitFor(() => {
      expect(screen.getByText("Athlete Profile & Settings")).toBeInTheDocument();
    });
    // Sign Out button in settings footer
    const signOutBtns = screen.getAllByRole("button", { name: "Sign Out" });
    fireEvent.click(signOutBtns[signOutBtns.length - 1]);
    unmountCloud();
  }, 15000);

  it("covers App.tsx error branches, local MP4 live sim, cloud connection test, fullscreen, popstate, and custom match delete", async () => {
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
              public_url: "http://127.0.0.1:8090",
              default_profile: "local",
            }),
            { status: 200 },
          );
        }
        if (url.includes("/api/auth/me")) {
          return new Response(
            JSON.stringify({
              email: "coach@example.com",
              name: "Coach",
              picture: "https://example.com/a.png",
              auth_mode: "local_bypass",
              role: "admin",
              is_admin: true,
            }),
            { status: 200 },
          );
        }
        if (url.includes("/api/matches") && init?.method === "DELETE") {
          return new Response(JSON.stringify({ ok: true, match_id: "custom_100" }), { status: 200 });
        }
        if (url.includes("/api/matches") && !url.includes("/share") && !url.includes("/moments") && !url.includes("/review")) {
          return new Response(
            JSON.stringify({ default_match_id: "custom_100", matches: [CUSTOM_MATCH] }),
            { status: 200 },
          );
        }
        if (url.includes("/api/system/status")) {
          return new Response(
            JSON.stringify({
              environment: "local",
              active_profile: "cloud",
              ollama_base_url: "http://127.0.0.1:11434",
              gemini_key_configured: false,
              masked_gemini_key: "",
              models: {},
              providers: { cloud_gemini_configured: false, ollama_reachable: false, ollama_models: [] },
            }),
            { status: 200 },
          );
        }
        if (url.includes("/api/system/test-connection")) {
          return new Response(
            JSON.stringify({ ok: true, target: "cloud", status: "connected", model: "gemini-4", latency_ms: 90, detail: "ok" }),
            { status: 200 },
          );
        }
        if (url.includes("/api/team/roster")) {
          return new Response(
            JSON.stringify({
              roster: [
                {
                  match_id: "custom_100",
                  title: "Custom Match · Final",
                  owner_email: "coach@example.com",
                  athlete_name: "Alex",
                  belt_label: "Blue Belt",
                  ruleset: "ibjjf_v6",
                  review_status: "needs_review",
                  pinned_drills: [],
                  created_at: "2026-11-27",
                },
              ],
            }),
            { status: 200 },
          );
        }
        if (url.includes("/api/knowledge/curricula")) {
          return new Response(
            JSON.stringify({
              version: "v0.1.0",
              count: 1,
              curricula: [
                {
                  doc_id: "c1",
                  title: "Sit-Up Sweep",
                  curriculum: "CTA",
                  position: "open_guard",
                  timestamp_sec: 120,
                  source_url: "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                  text_preview: "Sit-up sweep from open guard",
                },
                {
                  doc_id: "c2",
                  title: "Text Note Only",
                  curriculum: "OSJJ",
                  position: "general",
                  timestamp_sec: null,
                  source_url: "",
                  text_preview: "Concept notes",
                },
              ],
            }),
            { status: 200 },
          );
        }
        if (url.includes("/api/media/slowmo")) {
          return new Response(JSON.stringify({ filename: "c.mp4", url: "/api/media/files/c.mp4" }), { status: 200 });
        }
        // Fail everything else to exercise all error catch branches!
        return new Response(JSON.stringify({ detail: "Simulated error" }), { status: 500 });
      }),
    );

    vi.stubGlobal("URL", Object.assign(URL, { createObjectURL: vi.fn(() => "blob:local-mp4"), revokeObjectURL: vi.fn() }));
    const wsHolder: { current: { onopen?: () => void; onmessage?: (e: { data: string }) => void; close: ReturnType<typeof vi.fn> } | null } = {
      current: null,
    };
    class _WS {
      readyState = 1;
      onopen?: () => void;
      onmessage?: (e: { data: string }) => void;
      close = vi.fn();
      constructor() {
        wsHolder.current = this;
        setTimeout(() => this.onopen?.(), 0);
      }
      send() {}
    }
    vi.stubGlobal("WebSocket", _WS);
    Object.defineProperty(navigator, "mediaDevices", {
      value: { getUserMedia: vi.fn().mockRejectedValue(new Error("denied")) },
      configurable: true,
    });

    const { container } = render(<App />);
    await waitFor(() => {
      expect(screen.getByTestId("row-athlete-a")).toBeInTheDocument();
    });

    // Video loadedMetadata + pause + fullscreen request & exit + scrubber pin click
    const videoEl = container.querySelector("video") as HTMLVideoElement;
    Object.defineProperty(videoEl, "duration", { value: 120, configurable: true });
    Object.defineProperty(videoEl, "paused", { value: false, configurable: true });
    vi.spyOn(HTMLMediaElement.prototype, "pause").mockImplementation(() => {});
    fireEvent.loadedMetadata(videoEl);
    fireEvent.click(screen.getByLabelText("Play video"));

    const frame = container.querySelector(".video-frame") as HTMLElement;
    frame.requestFullscreen = vi.fn().mockResolvedValue(undefined);
    fireEvent.click(screen.getByLabelText("Toggle fullscreen"));
    Object.defineProperty(document, "fullscreenElement", { value: frame, configurable: true });
    document.exitFullscreen = vi.fn().mockResolvedValue(undefined);
    fireEvent.click(screen.getByLabelText("Toggle fullscreen"));
    Object.defineProperty(document, "fullscreenElement", { value: null, configurable: true });

    // Click scrubber pin + open Rule Peek without rule_info (fallback excerpt) + cached GIF reopen
    const firstPin = container.querySelector(".scrubber-pin-wrap") as HTMLElement;
    fireEvent.click(firstPin);
    fireEvent.click(screen.getByTestId("rule-cite-btn"));
    expect(screen.getByTestId("rule-peek-box")).toHaveTextContent("Official IBJJF Rulebook v6.1 reference");
    fireEvent.click(screen.getByTestId("highlight-btn"));
    await waitFor(() => expect(screen.getByTestId("highlight-video")).toBeInTheDocument());
    fireEvent.click(screen.getByTestId("highlight-btn"));
    fireEvent.click(screen.getByTestId("highlight-btn"));
    expect(screen.getByTestId("highlight-video")).toBeInTheDocument();

    // Error catches: Share failed, Moment Feedback offline fallback
    fireEvent.click(screen.getByTestId("share-match-btn"));
    await waitFor(() => expect(screen.getByTestId("share-match-btn")).toHaveTextContent("Share failed"));
    fireEvent.click(screen.getByTestId("moment-feedback-btn"));
    fireEvent.change(screen.getByLabelText("Moment Feedback Note"), { target: { value: "Offline note" } });
    fireEvent.click(screen.getByTestId("submit-feedback-btn"));
    await waitFor(() => expect(screen.getByTestId("moment-feedback-saved")).toBeInTheDocument());

    // Live Camera denied catch + Simulate Live from Local MP4 + B-actor WS HUD + invalid JSON WS + Stop Live revokes blob
    fireEvent.click(screen.getByRole("button", { name: /Analyze or Live Camera/i }));
    fireEvent.click(screen.getByRole("tab", { name: "Live Camera" }));
    fireEvent.click(screen.getByText("Use Live Camera"));
    await waitFor(() => expect(screen.queryByTestId("upload-match-modal")).not.toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: /Analyze or Live Camera/i }));
    fireEvent.click(screen.getByRole("tab", { name: "Live Camera" }));
    const simFileInput = screen.getByLabelText(/Simulate Live from Local MP4/i) as HTMLInputElement;
    fireEvent.change(simFileInput, { target: { files: [new File(["x"], "sim.mp4", { type: "video/mp4" })] } });
    await waitFor(() => expect(screen.getByTestId("live-sim-badge")).toBeInTheDocument());
    wsHolder.current?.onmessage?.({
      data: JSON.stringify({
        type: "hud",
        scoreboard: {
          athlete_a: { points: 0, advantages: 0, penalties: 0 },
          athlete_b: { points: 2, advantages: 1, penalties: 1 },
          timeline: [
            { event_id: "b1", actor: "B", raw_kind: "sweep", awarded_points: 2, timestamp_sec: 10 },
            { event_id: "b2", actor: "B", raw_kind: "near_pass", awarded_advantages: 1, timestamp_sec: 20 },
            { event_id: "b3", actor: "B", raw_kind: "stalling", awarded_penalties: 1, timestamp_sec: 30 },
          ],
        },
      }),
    });
    wsHolder.current?.onmessage?.({ data: "{bad-json" });
    fireEvent.click(screen.getByTestId("stop-live-btn"));

    // Settings error catches + Cloud test connection + ML Admin button
    fireEvent.click(screen.getByTestId("user-profile"));
    await waitFor(() => expect(screen.getByTestId("save-athlete-profile-btn")).toBeInTheDocument());
    fireEvent.click(screen.getByTestId("save-athlete-profile-btn"));
    await waitFor(() => expect(screen.getByTestId("profile-save-msg")).toHaveTextContent("Could not save profile."));
    fireEvent.click(screen.getByText("Cloud (Gemini 4 Tiered)"));
    await waitFor(() => expect(screen.getByText("Test Cloud Connection")).toBeInTheDocument());
    fireEvent.click(screen.getByText("Test Cloud Connection"));
    await waitFor(() => expect(screen.getByText(/connected · gemini-4/i)).toBeInTheDocument());
    fireEvent.click(screen.getByTestId("save-academy-btn"));
    fireEvent.change(screen.getByLabelText("Reference Title"), { target: { value: "Pass" } });
    fireEvent.click(screen.getByTestId("auto-tag-ref-btn"));
    fireEvent.click(screen.getByTestId("add-reference-btn"));
    fireEvent.click(screen.getByTestId("settings-curricula-btn"));
    await waitFor(() => expect(screen.getByTestId("curricula-view")).toBeInTheDocument());
    await waitFor(() => expect(screen.getByRole("button", { name: "Watch" })).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Watch" }));
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    fireEvent.change(screen.getByTestId("curricula-search-input"), { target: { value: "sweep" } });

    // popstate to /curricula, /me, /m/custom_100, and /unknown
    window.history.pushState({}, "", "/curricula");
    fireEvent(window, new PopStateEvent("popstate"));
    window.history.pushState({}, "", "/me");
    fireEvent(window, new PopStateEvent("popstate"));
    window.history.pushState({}, "", "/m/custom_100");
    fireEvent(window, new PopStateEvent("popstate"));
    window.history.pushState({}, "", "/unknown-route");
    fireEvent(window, new PopStateEvent("popstate"));

    // Overview Home -> Add to Film Study + Delete custom match
    fireEvent.click(screen.getByTestId("brand-home-btn"));
    await waitFor(() => expect(screen.getByTestId("overview-home")).toBeInTheDocument());
    fireEvent.click(screen.getByTestId("add-film-study-btn"));
    fireEvent.click(screen.getByRole("tab", { name: "Link (Drive / URL)" }));
    fireEvent.change(screen.getByLabelText("Google Drive Video URL"), { target: { value: "https://drive.google.com/file/d/12345678901234567890/view" } });
    fireEvent.click(screen.getByTestId("import-drive-btn"));
    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    // Keyboard Enter on Delete custom match + delete match error catch
    const delBtn = screen.getByTestId("delete-match-custom_100");
    fireEvent.keyDown(delBtn, { key: "Enter" });
  }, 20000);

  it("covers remaining App.tsx branches: clock anchors, webkitRequestFullscreen, local desktop errors, revoke error, upload error, and scrubber pin button", async () => {
    const matchWithSingleAnchor: ShowcaseMatch = {
      ...CUSTOM_MATCH,
      id: "custom_anchor_1",
      clock_anchors: [[10, 290]],
    };
    const match2: ShowcaseMatch = {
      ...CUSTOM_MATCH,
      id: "custom_anchor_2",
      bucket: "film_study",
      athlete_a: { name: "Second Fighter", belt: "black", gi: "black" },
    };

    let shareShouldFail = false;
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
              public_url: "http://127.0.0.1:8090",
              default_profile: "local",
            }),
            { status: 200 },
          );
        }
        if (url.includes("/api/auth/me")) {
          return new Response(
            JSON.stringify({
              email: "coach@example.com",
              name: "Coach",
              picture: "",
              auth_mode: "local_bypass",
              role: "admin",
              is_admin: true,
            }),
            { status: 200 },
          );
        }
        if (url.includes("/api/matches") && init?.method === "DELETE") {
          return new Response(JSON.stringify({ ok: true, match_id: "custom_anchor_1" }), { status: 200 });
        }
        if (url.includes("/api/matches/custom_anchor_1/share") && init?.method === "POST") {
          return new Response(JSON.stringify({ token: "t_rev", share_path: "/share/t_rev", share_url: "http://x/share/t_rev" }), { status: 200 });
        }
        if (url.includes("/api/share/t_rev") && init?.method === "DELETE") {
          if (shareShouldFail) return new Response(JSON.stringify({ detail: "fail" }), { status: 500 });
          return new Response(JSON.stringify({ ok: true }), { status: 200 });
        }
        if (url.includes("/api/matches/custom_anchor_1/moments") && init?.method === "PUT") {
          return new Response(JSON.stringify({ ok: true }), { status: 200 });
        }
        if (url.includes("/api/matches") && !url.includes("/review")) {
          return new Response(
            JSON.stringify({ default_match_id: "custom_anchor_1", matches: [matchWithSingleAnchor, match2] }),
            { status: 200 },
          );
        }
        if (url.includes("/api/academies/me/references") && init?.method === "POST") {
          return new Response(JSON.stringify({ ok: true }), { status: 200 });
        }
        if (url.includes("/api/local/folder/scan")) {
          return new Response(JSON.stringify({ detail: "Scan err" }), { status: 500 });
        }
        if (url.includes("/api/local/folder/analyze")) {
          return new Response(JSON.stringify({ detail: "Analyze err" }), { status: 500 });
        }
        if (url.includes("/api/local/academy/import-folder")) {
          return new Response(JSON.stringify({ detail: "Import err" }), { status: 500 });
        }
        if (url.includes("/api/team/roster")) {
          return new Response(
            JSON.stringify({
              roster: [
                { match_id: "custom_anchor_1", title: "M1", owner_email: "coach@example.com", athlete_name: "A", belt_label: "B", ruleset: "ibjjf_v6", review_status: "needs_review", pinned_drills: [], created_at: "now" },
                { match_id: "other_2", title: "M2", owner_email: "coach@example.com", athlete_name: "B", belt_label: "B", ruleset: "ibjjf_v6", review_status: "reviewed", pinned_drills: [], created_at: "now" },
              ],
            }),
            { status: 200 },
          );
        }
        if (url.includes("/review") && init?.method === "POST") {
          return new Response(JSON.stringify({ match_id: "custom_anchor_1", review_status: "reviewed", pinned_drills: [{ title: "D", url: "u" }] }), { status: 200 });
        }
        return new Response(JSON.stringify({ detail: "err" }), { status: 500 });
      }),
    );

    const { container } = render(<App />);
    await waitFor(() => expect(screen.getByTestId("row-athlete-a")).toBeInTheDocument());

    // Advance video time past end anchor to hit L178-179, click pin button (L2738-2740)
    const videoEl = container.querySelector("video") as HTMLVideoElement & { webkitRequestFullscreen?: () => void };
    Object.defineProperty(videoEl, "currentTime", { value: 200, writable: true, configurable: true });
    fireEvent.timeUpdate(videoEl);
    const pinBtn = container.querySelector(".scrubber-pin-wrap button") as HTMLElement | null;
    if (pinBtn) fireEvent.click(pinBtn);

    // webkitRequestFullscreen fallback (L906-907)
    const frame = container.querySelector(".video-frame") as HTMLElement & { requestFullscreen?: unknown };
    (frame as unknown as Record<string, unknown>).requestFullscreen = undefined;
    videoEl.webkitRequestFullscreen = vi.fn();
    fireEvent.click(screen.getByLabelText("Toggle fullscreen"));
    expect(videoEl.webkitRequestFullscreen).toHaveBeenCalled();

    // Share + Revoke failed catch (L1255-1257)
    shareShouldFail = true;
    fireEvent.click(screen.getByTestId("share-match-btn"));
    await waitFor(() => expect(screen.getByTestId("revoke-share-btn")).toBeInTheDocument());
    fireEvent.click(screen.getByTestId("revoke-share-btn"));
    await waitFor(() => expect(screen.getByTestId("share-match-btn")).toHaveTextContent("Revoke failed"));

    // Read-only ruleset pill check (#267)
    expect(screen.getByTestId("match-ruleset-pill")).toBeInTheDocument();

    // Upload file error catch (L1470)
    fireEvent.click(screen.getByRole("button", { name: /Analyze or Live Camera/i }));
    const fileInput = screen.getByTestId("camera-roll-file-input") as HTMLInputElement;
    fireEvent.change(fileInput, { target: { files: [new File(["x"], "fail.mp4", { type: "video/mp4" })] } });
    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Close" }));

    // Settings -> local folder scan error, import notes error, add reference fallback without `reference` field (L1048, L1089, L1119-1124)
    fireEvent.click(screen.getByTestId("user-profile"));
    await waitFor(() => expect(screen.getByTestId("scan-local-folder-btn")).toBeInTheDocument());
    fireEvent.click(screen.getByTestId("scan-local-folder-btn"));
    await waitFor(() => expect(screen.getByTestId("local-desktop-msg")).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText("Local Academy Notes Folder"), { target: { value: "/tmp/notes" } });
    fireEvent.click(screen.getByTestId("import-local-academy-btn"));
    fireEvent.change(screen.getByLabelText("Reference Title"), { target: { value: "Armbar" } });
    fireEvent.click(screen.getByTestId("add-reference-btn"));
    fireEvent.click(screen.getByRole("button", { name: "Close" }));

    // Overview Home -> film study summary error catch (L786) + delete active match switches to remaining[0]
    fireEvent.click(screen.getByTestId("brand-home-btn"));
    await waitFor(() => expect(screen.getByTestId("overview-home")).toBeInTheDocument());
    fireEvent.click(screen.getByTestId("film-study-filter-second-fighter"));
    fireEvent.click(screen.getByTestId("delete-match-custom_anchor_1"));
  }, 20000);

  it("closes final remaining App.tsx lines for literal 100.0% line coverage", async () => {
    const emptyEventsMatch = {
      ...CUSTOM_MATCH,
      id: "custom_empty_ev_1",
      title: "Empty Events Match",
      pill: "Empty Ev",
      clock_anchors: [],
      video_start_sec: 0,
      video_end_sec: 120,
      regulation_sec: 300,
      events: [],
    };
    const secondMatch = {
      ...CUSTOM_MATCH,
      id: "second_m_2",
      title: "Second Match",
      pill: "Second",
    };

    // 1. Share banner brand-home-btn click (L2052-2053)
    window.history.pushState({}, "", "/share/tok-brand-clear");
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes("/api/share/tok-brand-clear")) {
          return new Response(
            JSON.stringify({ token: "tok-brand-clear", academy_name: "Test Academy", match: emptyEventsMatch }),
            { status: 200 },
          );
        }
        return new Response(
          JSON.stringify({
            environment: "local",
            allow_local_dev_bypass: true,
            auth_required: false,
            google_client_id: "",
            public_url: "http://127.0.0.1:8090",
            default_profile: "local",
          }),
          { status: 200 },
        );
      }),
    );
    const { unmount: unmountShareBrand } = render(<App />);
    await waitFor(() => expect(screen.getByTestId("shared-breakdown-banner")).toBeInTheDocument());
    fireEvent.click(screen.getByTestId("brand-home-btn"));
    unmountShareBrand();

    // 2. GIS polling timer succeeds on later tick (L673-674)
    window.history.pushState({}, "", "/");
    delete (window as unknown as { google?: unknown }).google;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes("/api/auth/config")) {
          return new Response(
            JSON.stringify({
              environment: "cloud",
              google_client_id: "cid-gis-tick",
              allow_local_dev_bypass: false,
              auth_required: true,
              public_url: "https://example.com",
              default_profile: "cloud",
            }),
            { status: 200 },
          );
        }
        return new Response(JSON.stringify({}), { status: 401 });
      }),
    );
    const { unmount: unmountGis } = render(<App />);
    await waitFor(() => expect(screen.getByTestId("demo-sebastian-btn")).toBeInTheDocument());
    (window as unknown as { google: unknown }).google = {
      accounts: { id: { initialize: vi.fn(), renderButton: vi.fn(), disableAutoSelect: vi.fn() } },
    };
    await new Promise((r) => setTimeout(r, 220));
    unmountGis();

    // 3. Player + empty events anchor (L159-161) + popstate on "/" (L748) + live sim WS 0-pt event (L313) +
    //    canvas drawImage catch (L1717) + stopLiveSession close/pause catches via handleSelectMatch (L932, L946, L967-968) +
    //    local video analyze error catch (L1068) + delete match error catch (L1272) + signOut while playing (L1742-1747)
    const wsHolder: { current: { onopen?: () => void; onmessage?: (e: { data: string }) => void; onerror?: () => void; close: ReturnType<typeof vi.fn> } | null } = {
      current: null,
    };
    class _ThrowingWS {
      static OPEN = 1;
      readyState = 1;
      onopen?: () => void;
      onmessage?: (e: { data: string }) => void;
      onerror?: () => void;
      send = vi.fn();
      close = vi.fn(() => {
        throw new Error("ws close fail");
      });
      constructor() {
        wsHolder.current = this;
      }
    }
    vi.stubGlobal("WebSocket", _ThrowingWS as unknown as typeof WebSocket);
    vi.stubGlobal("URL", Object.assign(URL, { createObjectURL: vi.fn(() => "blob:sim-2"), revokeObjectURL: vi.fn() }));
    const origGetContext = HTMLCanvasElement.prototype.getContext;
    HTMLCanvasElement.prototype.getContext = vi.fn(() => ({
      drawImage: () => {
        throw new Error("canvas draw fail");
      },
    })) as unknown as typeof HTMLCanvasElement.prototype.getContext;

    setStoredToken("tok-signout-test");
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.includes("/api/auth/config")) {
          return new Response(
            JSON.stringify({
              environment: "local",
              allow_local_dev_bypass: true,
              auth_required: true,
              google_client_id: "",
              public_url: "http://127.0.0.1:8090",
              default_profile: "local",
            }),
            { status: 200 },
          );
        }
        if (url.includes("/api/auth/me")) {
          return new Response(
            JSON.stringify({
              email: "coach@example.com",
              name: "Coach",
              picture: "",
              auth_mode: "google_id_token",
              role: "admin",
              is_admin: true,
            }),
            { status: 200 },
          );
        }
        if (url.includes("/api/matches") && init?.method === "DELETE") {
          return new Response(JSON.stringify({ detail: "cannot delete" }), { status: 500 });
        }
        if (url.includes("/api/matches") && !url.includes("/share") && !url.includes("/review")) {
          return new Response(
            JSON.stringify({ default_match_id: "custom_empty_ev_1", matches: [emptyEventsMatch, secondMatch] }),
            { status: 200 },
          );
        }
        if (url.includes("/api/local/folder/scan")) {
          return new Response(
            JSON.stringify({
              folder: "/tmp/vids",
              count: 1,
              videos: [{ path: "/tmp/vids/fail_offline.mp4", filename: "fail_offline.mp4", size_mb: 1.2, modified_at: "now" }],
            }),
            { status: 200 },
          );
        }
        if (url.includes("/api/local/folder/analyze")) {
          return new Response(JSON.stringify({ detail: "Offline analyze failed" }), { status: 500 });
        }
        return new Response(JSON.stringify({}), { status: 200 });
      }),
    );

    const { container, unmount } = render(<App />);
    await waitFor(() => expect(screen.getByTestId("row-athlete-a")).toBeInTheDocument());

    // popstate while already in player mode on "/" (L748)
    window.history.pushState({}, "", "/");
    fireEvent(window, new PopStateEvent("popstate"));

    // Start live sim from Add Match modal -> trigger WS 0-point event (L313) + canvas drawImage throw (L1717)
    fireEvent.click(screen.getByRole("button", { name: /Analyze or Live Camera/i }));
    fireEvent.click(screen.getByRole("tab", { name: "Live Camera" }));
    const simInput = screen.getByLabelText(/Simulate Live from Local MP4/i) as HTMLInputElement;
    fireEvent.change(simInput, { target: { files: [new File(["mp4"], "sim2.mp4", { type: "video/mp4" })] } });
    await waitFor(() => expect(wsHolder.current).not.toBeNull());
    wsHolder.current?.onopen?.();
    wsHolder.current?.onmessage?.({ data: JSON.stringify({ type: "error", detail: "Vision error" }) });
    wsHolder.current?.onerror?.();
    wsHolder.current?.onmessage?.({
      data: JSON.stringify({
        type: "hud",
        scoreboard: {
          athlete_a: { points: 0, advantages: 0, penalties: 0 },
          athlete_b: { points: 0, advantages: 0, penalties: 0 },
          timeline: [
            {
              event_id: "ev-neutral",
              actor: "A",
              raw_kind: "guard_pull",
              awarded_points: 0,
              awarded_advantages: 0,
              awarded_penalties: 0,
              timestamp_sec: 12,
              match_clock_str: "04:48",
            },
          ],
        },
      }),
    });

    const videoEl = container.querySelector("video") as HTMLVideoElement;
    Object.defineProperty(videoEl, "readyState", { value: 4, configurable: true });
    Object.defineProperty(videoEl, "videoWidth", { value: 320, configurable: true });
    Object.defineProperty(videoEl, "videoHeight", { value: 240, configurable: true });
    Object.defineProperty(videoEl, "paused", { value: false, configurable: true });
    videoEl.pause = vi.fn(() => {
      throw new Error("pause fail");
    });
    await new Promise((r) => setTimeout(r, 560));

    // Switch match while liveSimActive -> invokes stopLiveSession (L967-968, L932, L946)
    fireEvent.change(screen.getByLabelText("Match Selector"), { target: { value: "second_m_2" } });

    // Settings -> scan local folder (returns 1 video) -> click Analyze Offline (fails -> L1068)
    fireEvent.click(screen.getByTestId("user-profile"));
    await waitFor(() => expect(screen.getByTestId("scan-local-folder-btn")).toBeInTheDocument());
    fireEvent.click(screen.getByTestId("scan-local-folder-btn"));
    await waitFor(() => expect(screen.getByTestId("analyze-offline-btn")).toBeInTheDocument());
    fireEvent.click(screen.getByTestId("analyze-offline-btn"));
    await waitFor(() => expect(screen.getByTestId("local-desktop-msg")).toBeInTheDocument());

    // Sign out while videoRef.current && !videoRef.current.paused && pause() throws (L1742-1747)
    const activeVideo = container.querySelector("video") as HTMLVideoElement;
    Object.defineProperty(activeVideo, "paused", { value: false, configurable: true });
    activeVideo.pause = vi.fn(() => {
      throw new Error("signout pause fail");
    });
    const signOutBtns = screen.getAllByRole("button", { name: "Sign Out" });
    fireEvent.click(signOutBtns[signOutBtns.length - 1]);
    unmount();

    // Direct /m/<id> load preserves URL match ID (#274/#280) + delete match error catch (L1272)
    window.history.pushState({}, "", "/m/second_m_2");
    setStoredToken("tok-del");
    const { unmount: unmountDel } = render(<App />);
    await waitFor(() => expect(screen.getByLabelText("Match Selector")).toHaveValue("second_m_2"));
    fireEvent.click(screen.getByTestId("brand-home-btn"));
    await waitFor(() => expect(screen.getByTestId("delete-match-custom_empty_ev_1")).toBeInTheDocument());
    fireEvent.click(screen.getByTestId("delete-match-custom_empty_ev_1"));
    await new Promise((r) => setTimeout(r, 50));
    unmountDel();

    HTMLCanvasElement.prototype.getContext = origGetContext;
  }, 20000);
});
