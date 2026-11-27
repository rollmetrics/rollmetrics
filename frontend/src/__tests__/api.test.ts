import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  api,
  getStoredToken,
  mapAdjudicatedSnapshotToMatch,
  setStoredToken,
  withAuthUrl,
} from "../api";

describe("RollMetrics API client & snapshot mapper", () => {
  beforeEach(() => {
    setStoredToken("");
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("manages auth tokens with and without localStorage and appends query tokens cleanly", () => {
    const store = new Map<string, string>();
    vi.stubGlobal("localStorage", {
      getItem: (k: string) => store.get(k) ?? null,
      setItem: (k: string, v: string) => {
        store.set(k, v);
      },
      removeItem: (k: string) => {
        store.delete(k);
      },
    });
    expect(getStoredToken()).toBe("");
    expect(withAuthUrl("/api/matches/mc9/video")).toBe("/api/matches/mc9/video");

    setStoredToken("abc+123/=");
    expect(getStoredToken()).toBe("abc+123/=");
    expect(withAuthUrl("/api/matches/mc9/video")).toBe(
      "/api/matches/mc9/video?token=abc%2B123%2F%3D",
    );
    expect(withAuthUrl("/api/matches/mc9/video?foo=1")).toBe(
      "/api/matches/mc9/video?foo=1&token=abc%2B123%2F%3D",
    );

    setStoredToken("");
    expect(getStoredToken()).toBe("");

    // Fallback when localStorage throws
    vi.stubGlobal("localStorage", {
      getItem: () => {
        throw new Error("blocked");
      },
      setItem: () => {
        throw new Error("blocked");
      },
      removeItem: () => {
        throw new Error("blocked");
      },
    });
    setStoredToken("mem-only");
    expect(getStoredToken()).toBe("mem-only");
    setStoredToken("");
  });

  it("maps adjudicated match snapshots with points, advantages, penalties, and opponent bonuses", () => {
    const mapped = mapAdjudicatedSnapshotToMatch(
      {
        video_url: "/api/media/files/match_1.mp4",
        video_path: "/tmp/match_1.mp4",
        snapshot: {
          winner: "B",
          win_method: "submission",
          division: {
            belt: "purple",
            uniform: "nogi",
            duration_sec: 420,
            athlete_a_name: "Alex Silva",
            athlete_b_name: "Jordan Cruz",
          },
          coaching_summary: [
            {
              title: "Back Control Finishes",
              source_url: "https://example.com/back",
            },
          ],
          timeline: [
            {
              event_id: "e1",
              actor: "A",
              raw_kind: "guard_pass",
              timestamp_sec: 45,
              awarded_points: 3,
              rule_article: "IBJJF Art. 4.2",
              cta_recommendations: [{ source_url: "https://example.com/pass" }],
            },
            {
              event_id: "e2",
              actor: "B",
              raw_kind: "near_sweep",
              timestamp_sec: 90,
              awarded_advantages: 1,
            },
            {
              event_id: "e3",
              actor: "A",
              raw_kind: "stalling",
              timestamp_sec: 120,
              awarded_penalties: 1,
              opponent_bonus_advantages: 1,
            },
            {
              event_id: "e4",
              actor: "B",
              raw_kind: "rear_naked_choke",
              timestamp_sec: 180,
              ended_match: true,
              opponent_bonus_points: 0,
            },
          ],
        },
      },
      "Silva vs. Cruz",
    );

    expect(mapped.belt).toBe("purple");
    expect(mapped.belt_label).toBe("Purple Belt · NOGI");
    expect(mapped.winner).toBe("B");
    expect(mapped.win_label).toBe("SUB");
    expect(mapped.video_url).toBe("/api/media/files/match_1.mp4");
    expect(mapped.events).toHaveLength(4);
    expect(mapped.events[0].call).toBe("+3 PTS");
    expect(mapped.events[0].pts_a).toBe(3);
    expect(mapped.events[1].call).toBe("+1 ADV");
    expect(mapped.events[1].adv_b).toBe(1);
    expect(mapped.events[2].call).toBe("+1 PEN");
    expect(mapped.events[2].pen_a).toBe(1);
    expect(mapped.events[2].adv_b).toBe(2);
    expect(mapped.events[3].is_finish).toBe(true);
    expect(mapped.coach_summary?.athlete_a.cta_links[0].label).toBe("Back Control Finishes");
  });

  it("calls all REST endpoints with Bearer header and surfaces server error details", async () => {
    setStoredToken("my-jwt");
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/api/fail-json")) {
        return new Response(JSON.stringify({ detail: "Custom forbidden error" }), { status: 403 });
      }
      if (url.includes("/api/fail-plain")) {
        return new Response("Internal error", { status: 500 });
      }
      const auth = new Headers(init?.headers || {}).get("Authorization");
      return new Response(JSON.stringify({ ok: true, url, auth, references: [] }), {
        status: 200,
      });
    });
    vi.stubGlobal("fetch", fetchMock);

    await api.getAuthConfig();
    await api.getMe();
    await api.getMatches();
    await api.getPersonalCoachingSummary();
    await api.getFilmStudySummary("Sebastian Bussieres");
    await api.getAdminOverview();
    await api.getMyAcademy();
    await api.updateMyAcademy({ academy_name: "Alliance Montreal" });
    await api.getMyAcademyReferences();
    await api.addMyAcademyReference({ title: "Knee Cut", position: "open_guard" });
    await api.updateMatchBucket("mc9_semifinal", "my_matches");
    await api.getSystemStatus();
    await api.switchProfile("cloud");
    await api.saveSystemConfig({ profile: "local", ollama_base_url: "http://127.0.0.1:11434" });
    await api.testConnection({ target: "local" });
    await api.analyzeUpload(new File(["abc"], "match.mp4", { type: "video/mp4" }), "local");
    await api.analyzeUrl("https://youtube.com/watch?v=123", "cloud");
    await api.generateBoard({ match_id: "mc9_semifinal", center_timestamp_sec: 50, title: "Test", cues: [] });
    await api.generateSlowmo({ match_id: "mc9_semifinal", start_sec: 48, duration_sec: 5, speed: 0.4 });
    await api.initUpload({
      filename: "iphone.mov",
      content_type: "video/quicktime",
      size_bytes: 1024,
      bucket: "my_matches",
      uniform: "nogi",
      belt: "white",
      athlete_a_name: "A",
      athlete_b_name: "B",
      athlete_a_gi: "white",
      athlete_b_gi: "blue",
    });
    await api.completeUpload({ upload_id: "upl_123", profile: "cloud" });
    await api.uploadGoogleDrive({
      drive_url: "https://drive.google.com/file/d/1234567890abcdef/view",
      bucket: "my_matches",
      uniform: "gi",
      belt: "white",
      athlete_a_name: "A",
      athlete_b_name: "B",
      athlete_a_gi: "white",
      athlete_b_gi: "blue",
    });
    await api.createShareLink("mc9_semifinal");
    await api.getSharedMatch("tok123");
    await api.generateSharedHighlight("tok123", { start_sec: 10, duration_sec: 5, speed: 0.4 });
    await api.getLocalHardware();
    await api.getCurricula();
    await api.getCurricula("sweep");
    await api.scanLocalFolder("~/Videos/BJJ");
    await api.analyzeLocalVideo({ video_path: "/tmp/roll.mp4", title: "Local Roll" });
    await api.importLocalAcademyFolder({ folder_path: "/tmp/notes", academy_id: "cta" });
    await api.demoLogin();
    await api.getIbjjfRulebook();
    await api.updateMyProfile({ name: "Alex", belt: "blue", academy: "Plateau BJJ", coach: "Chris B" });
    await api.deleteMatch("custom_1");
    await api.revokeShareLink("tok123");
    await api.updateMatchMoments("mc9_semifinal", { events: [], ruleset: "adcc" });
    await api.submitMomentFeedback("mc9_semifinal", "ev1", "Check timing");
    await api.getTeamRoster();
    await api.updateMatchReview("mc9_semifinal", { review_status: "reviewed" });
    await api.autoTagAcademyReference({ url: "https://youtu.be/dQw4w9WgXcQ", title: "Knee Cut" });
    expect(api.getExportDataUrl()).toContain("/api/me/export.zip");

    expect(fetchMock).toHaveBeenCalled();
    const firstCallHeaders = new Headers(fetchMock.mock.calls[0][1]?.headers || {});
    expect(firstCallHeaders.get("Authorization")).toBe("Bearer my-jwt");

    // Verify error handling
    fetchMock.mockResolvedValueOnce(
      new Response(JSON.stringify({ detail: "Forbidden by allowlist" }), { status: 403 }),
    );
    await expect(api.getMe()).rejects.toThrow("Forbidden by allowlist");

    fetchMock.mockResolvedValueOnce(new Response("bad gateway", { status: 502 }));
    await expect(api.getMe()).rejects.toThrow("HTTP 502");
  });
});
