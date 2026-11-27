import type {
  AcademyProfile,
  AcademyReference,
  AdminOverviewResponse,
  AthleteAnchorPoint,
  AuthConfig,
  AuthUser,
  ConnectionTestResult,
  FilmStudySummary,
  LocalAcademyImportResponse,
  LocalFolderScanResponse,
  LocalHardwareStatus,
  MatchBucket,
  ModelProfile,
  PersonalCoachingSummary,
  PinnedDrill,
  RulesetId,
  SharedMatchPayload,
  ShareLinkResponse,
  ShowcaseEvent,
  ShowcaseMatch,
  SystemStatusResponse,
  TeamRosterItem,
  UploadInitResponse,
} from "./types";

const TOKEN_STORAGE_KEY = "rollmetrics_google_id_token";
let memoryToken = "";

export function getStoredToken(): string {
  try {
    if (typeof localStorage !== "undefined" && typeof localStorage.getItem === "function") {
      return localStorage.getItem(TOKEN_STORAGE_KEY) || memoryToken;
    }
  } catch {
    // Ignore storage errors in restricted contexts
  }
  return memoryToken;
}

export function setStoredToken(token: string): void {
  memoryToken = token;
  try {
    if (typeof localStorage !== "undefined" && typeof localStorage.setItem === "function") {
      if (token) {
        localStorage.setItem(TOKEN_STORAGE_KEY, token);
      } else {
        localStorage.removeItem(TOKEN_STORAGE_KEY);
      }
    }
  } catch {
    // Ignore storage errors in restricted contexts
  }
}

export function withAuthUrl(path: string, token?: string): string {
  const activeToken = token ?? getStoredToken();
  if (!activeToken) return path;
  const sep = path.includes("?") ? "&" : "?";
  return `${path}${sep}token=${encodeURIComponent(activeToken)}`;
}

async function apiFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  const token = getStoredToken();
  const headers = new Headers(init.headers || {});
  if (token && !headers.has("Authorization")) {
    headers.set("Authorization", `Bearer ${token}`);
  }
  if (init.body && !(init.body instanceof FormData) && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  const resp = await fetch(path, { ...init, headers });
  if (!resp.ok) {
    let detail = `HTTP ${resp.status}`;
    try {
      const errJson = await resp.json();
      if (errJson?.detail) detail = String(errJson.detail);
    } catch {
      // Fallback to status string
    }
    throw new Error(detail);
  }
  return (await resp.json()) as T;
}

function fmtSec(sec: number): string {
  const s = Math.max(0, Math.round(sec));
  return `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
}

export function mapAdjudicatedSnapshotToMatch(
  res: Record<string, unknown>,
  label: string,
): ShowcaseMatch {
  const snap = ((res.snapshot as Record<string, unknown>) || res) as Record<string, unknown>;
  const div = (snap.division as Record<string, unknown>) || {};
  const timeline = (snap.timeline as Array<Record<string, unknown>>) || [];

  let ptsA = 0;
  let advA = 0;
  let penA = 0;
  let ptsB = 0;
  let advB = 0;
  let penB = 0;

  const events: ShowcaseEvent[] = timeline.map((ev, idx) => {
    const actor = String(ev.actor || "A") === "B" ? "B" : "A";
    const addPts = Number(ev.awarded_points || 0);
    const addAdv = Number(ev.awarded_advantages || 0);
    const addPen = Number(ev.awarded_penalties || 0);
    const oppPts = Number(ev.opponent_bonus_points || 0);
    const oppAdv = Number(ev.opponent_bonus_advantages || 0);

    if (actor === "A") {
      ptsA += addPts;
      advA += addAdv;
      penA += addPen;
      ptsB += oppPts;
      advB += oppAdv;
    } else {
      ptsB += addPts;
      advB += addAdv;
      penB += addPen;
      ptsA += oppPts;
      advA += oppAdv;
    }

    const rawKind = String(ev.raw_kind || "exchange");
    const call =
      addPts > 0
        ? `+${addPts} PTS`
        : addAdv > 0
          ? `+${addAdv} ADV`
          : addPen > 0
            ? `+${addPen} PEN`
            : rawKind.replace(/_/g, " ").toUpperCase();
    const recs = (ev.cta_recommendations as Array<Record<string, unknown>>) || [];
    const ts = Number(ev.timestamp_sec || 0);

    return {
      id: String(ev.event_id || `ev-${idx + 1}`),
      video_sec: ts,
      time_str: fmtSec(ts),
      clock_str: String(ev.match_clock_str || fmtSec(Math.max(0, 300 - ts))),
      actor,
      call,
      detail: rawKind.replace(/_/g, " "),
      rule: String(ev.rule_article || "IBJJF Art. 4"),
      cta_url: String(recs[0]?.source_url || "https://www.youtube.com/watch?v=4-7AYrBCOvQ"),
      pts_a: ptsA,
      adv_a: advA,
      pen_a: penA,
      pts_b: ptsB,
      adv_b: advB,
      pen_b: penB,
      control_3s: addPts > 0,
      is_finish: Boolean(ev.ended_match),
    };
  });

  const belt = (String(div.belt || "white").toLowerCase() as ShowcaseMatch["belt"]) || "white";
  const regSec = Number(div.duration_sec || 300);
  const nameA = String(div.athlete_a_name || "Athlete A");
  const nameB = String(div.athlete_b_name || "Athlete B");
  const coachingSummary = (snap.coaching_summary as Array<Record<string, unknown>>) || [];
  const topCtaLinks = coachingSummary.slice(0, 2).map((item) => ({
    label: String(item.title || "CTA Curriculum"),
    url: String(item.source_url || "https://www.youtube.com/watch?v=4-7AYrBCOvQ"),
  }));
  const defaultCta =
    topCtaLinks.length > 0
      ? topCtaLinks
      : [{ label: "CTA Guard & Passing Fundamentals", url: "https://www.youtube.com/watch?v=4-7AYrBCOvQ" }];

  return {
    id: `custom_${Date.now()}`,
    pill: label,
    title: label,
    belt,
    belt_label: `${belt[0].toUpperCase()}${belt.slice(1)} Belt · ${String(div.uniform || "gi").toUpperCase()}`,
    regulation_sec: regSec,
    video_duration_sec: regSec,
    video_start_sec: 0,
    video_end_sec: regSec,
    athlete_a: { name: nameA, belt, gi: "white" },
    athlete_b: { name: nameB, belt, gi: "blue" },
    winner: String(snap.winner || "A") === "B" ? "B" : "A",
    win_label: String(snap.win_method || "").toLowerCase().includes("sub") ? "SUB" : "WIN",
    video_url: res.video_url ? String(res.video_url) : undefined,
    video_path: res.video_path ? String(res.video_path) : undefined,
    coach_summary: {
      overview: `${nameA} and ${nameB} finished at ${ptsA}-${ptsB} points (${advA}-${advB} advantages, ${penA}-${penB} penalties). Control of grips and 3.0-second stabilization windows decided the key exchanges.`,
      athlete_a: {
        name: nameA,
        gi: "white",
        cues: [
          "Establish first grip before initiating guard pulls or level changes.",
          "Hold every scoring position for a full 3.0 seconds before chaining to the next attack.",
        ],
        cta_links: defaultCta,
      },
      athlete_b: {
        name: nameB,
        gi: "blue",
        cues: [
          "Win inside elbow space early to prevent opponent stabilization.",
          "Stay active in neutral tie-ups to avoid stalling penalties.",
        ],
        cta_links: defaultCta,
      },
    },
    events,
  };
}

export const api = {
  getAuthConfig: () => apiFetch<AuthConfig>("/api/auth/config"),
  getIbjjfRulebook: () =>
    apiFetch<{
      version: string;
      title: string;
      count: number;
      articles: Array<{ id: string; article: string; title: string; text: string; source_url: string }>;
    }>("/api/rules/ibjjf-v6-1"),
  getMe: () => apiFetch<AuthUser>("/api/auth/me"),
  updateMyProfile: (payload: {
    name?: string;
    picture?: string;
    belt?: string;
    academy?: string;
    coach?: string;
    academy_id?: string;
    academy_name?: string;
  }) =>
    apiFetch<AuthUser>("/api/me/profile", {
      method: "PUT",
      body: JSON.stringify(payload),
    }),
  demoLogin: () =>
    apiFetch<{ token: string; user: AuthUser }>("/api/auth/demo-login", {
      method: "POST",
    }),
  getMatches: () =>
    apiFetch<{ default_match_id: string; matches: ShowcaseMatch[] }>("/api/matches"),
  getPersonalCoachingSummary: () =>
    apiFetch<PersonalCoachingSummary>("/api/me/coaching-summary"),
  getFilmStudySummary: (athlete: string) =>
    apiFetch<FilmStudySummary>(
      `/api/film-study/summary?athlete=${encodeURIComponent(athlete)}`,
    ),
  getAdminOverview: () => apiFetch<AdminOverviewResponse>("/api/admin/overview"),
  getMlAdminOverview: () => apiFetch<AdminOverviewResponse>("/api/ml/overview"),
  getMyAcademy: () => apiFetch<AcademyProfile>("/api/academies/me"),
  updateMyAcademy: (payload: { academy_name: string; academy_id?: string }) =>
    apiFetch<AcademyProfile>("/api/academies/me", {
      method: "PUT",
      body: JSON.stringify(payload),
    }),
  getMyAcademyReferences: () =>
    apiFetch<{ academy_id?: string; academy_name?: string; references: AcademyReference[] }>(
      "/api/academies/me/references",
    ),
  addMyAcademyReference: (payload: {
    title: string;
    position?: string;
    notes?: string;
    source_url?: string;
    belt?: string;
  }) =>
    apiFetch<{ reference?: AcademyReference; ok?: boolean }>("/api/academies/me/references", {
      method: "POST",
      body: JSON.stringify({
        title: payload.title,
        text: (payload.notes || "").trim() || payload.title,
        position: payload.position || "all",
        source_url: payload.source_url || "",
        belt: payload.belt || "white",
        reference_type: payload.source_url ? "video" : "note",
      }),
    }),
  initUpload: (payload: {
    filename: string;
    content_type: string;
    size_bytes: number;
    bucket: MatchBucket;
    uniform: "gi" | "nogi";
    belt: string;
    athlete_a_name: string;
    athlete_b_name: string;
    athlete_a_gi: "white" | "blue" | "black";
    athlete_b_gi: "white" | "blue" | "black";
    title?: string;
    ruleset?: RulesetId;
    trim_start_sec?: number;
    trim_end_sec?: number;
    athlete_a_anchor?: AthleteAnchorPoint;
    athlete_b_anchor?: AthleteAnchorPoint;
  }) =>
    apiFetch<UploadInitResponse>("/api/uploads/init", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  completeUpload: (payload: { upload_id: string; profile?: ModelProfile }) =>
    apiFetch<{ match: ShowcaseMatch; snapshot: Record<string, unknown> }>("/api/uploads/complete", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  uploadGoogleDrive: (payload: {
    drive_url: string;
    title?: string;
    bucket: MatchBucket;
    uniform: "gi" | "nogi";
    belt: string;
    athlete_a_name: string;
    athlete_b_name: string;
    athlete_a_gi: "white" | "blue" | "black";
    athlete_b_gi: "white" | "blue" | "black";
    profile?: ModelProfile;
    ruleset?: RulesetId;
    trim_start_sec?: number;
    trim_end_sec?: number;
    athlete_a_anchor?: AthleteAnchorPoint;
    athlete_b_anchor?: AthleteAnchorPoint;
  }) =>
    apiFetch<{ match: ShowcaseMatch; snapshot: Record<string, unknown> }>(
      "/api/uploads/google-drive",
      {
        method: "POST",
        body: JSON.stringify(payload),
      },
    ),
  createShareLink: (matchId: string) =>
    apiFetch<ShareLinkResponse>(`/api/matches/${encodeURIComponent(matchId)}/share`, {
      method: "POST",
    }),
  revokeShareLink: (token: string) =>
    apiFetch<{ ok: boolean; token: string }>(`/api/share/${encodeURIComponent(token)}`, {
      method: "DELETE",
    }),
  deleteMatch: (matchId: string) =>
    apiFetch<{ ok: boolean; match_id: string }>(`/api/matches/${encodeURIComponent(matchId)}`, {
      method: "DELETE",
    }),
  updateMatchMoments: (
    matchId: string,
    payload: {
      events: ShowcaseEvent[];
      ruleset?: RulesetId;
      opt_in_feedback?: boolean;
    },
  ) =>
    apiFetch<{ match: ShowcaseMatch; feedback_logged?: boolean }>(
      `/api/matches/${encodeURIComponent(matchId)}/moments`,
      {
        method: "PUT",
        body: JSON.stringify(payload),
      },
    ),
  submitMomentFeedback: (matchId: string, eventId: string, note: string) =>
    apiFetch<{ ok: boolean; match_id: string; event_id: string; feedback_logged: boolean }>(
      `/api/matches/${encodeURIComponent(matchId)}/events/${encodeURIComponent(eventId)}/feedback`,
      {
        method: "POST",
        body: JSON.stringify({ note }),
      },
    ),
  getTeamRoster: () => apiFetch<{ roster: TeamRosterItem[] }>("/api/team/roster"),
  updateMatchReview: (
    matchId: string,
    payload: {
      review_status?: "needs_review" | "reviewed";
      pin_drill?: PinnedDrill;
    },
  ) =>
    apiFetch<{
      match_id: string;
      review_status: "needs_review" | "reviewed";
      pinned_drills: PinnedDrill[];
    }>(`/api/matches/${encodeURIComponent(matchId)}/review`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  autoTagAcademyReference: (payload: { url?: string; title?: string; notes?: string }) =>
    apiFetch<{ title: string; position: string; belt: string; tags: string[]; summary: string }>(
      "/api/academies/me/references/auto-tag",
      {
        method: "POST",
        body: JSON.stringify(payload),
      },
    ),
  getExportDataUrl: () => withAuthUrl("/api/me/export.zip"),
  getSharedMatch: (token: string) =>
    apiFetch<SharedMatchPayload>(`/api/share/${encodeURIComponent(token)}`),
  generateSharedHighlight: (
    token: string,
    payload: { start_sec: number; duration_sec: number; speed: number },
  ) =>
    apiFetch<{ filename: string; url: string }>(
      `/api/share/${encodeURIComponent(token)}/highlight`,
      {
        method: "POST",
        body: JSON.stringify(payload),
      },
    ),
  getLocalHardware: () => apiFetch<LocalHardwareStatus>("/api/local/hardware"),
  getCurricula: (q?: string) =>
    apiFetch<import("./types").CurriculaResponse>(
      `/api/knowledge/curricula${q ? `?q=${encodeURIComponent(q)}` : ""}`,
    ),
  scanLocalFolder: (folder_path: string) =>
    apiFetch<LocalFolderScanResponse>("/api/local/folder/scan", {
      method: "POST",
      body: JSON.stringify({ folder_path }),
    }),
  analyzeLocalVideo: (payload: {
    video_path: string;
    title?: string;
    belt?: string;
    uniform?: "gi" | "nogi";
    bucket?: MatchBucket;
  }) =>
    apiFetch<{ match: ShowcaseMatch; snapshot: Record<string, unknown> }>(
      "/api/local/folder/analyze",
      {
        method: "POST",
        body: JSON.stringify(payload),
      },
    ),
  importLocalAcademyFolder: (payload: {
    folder_path: string;
    academy_id?: string;
    academy_name?: string;
  }) =>
    apiFetch<LocalAcademyImportResponse>("/api/local/academy/import-folder", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  updateMatchBucket: (matchId: string, bucket: MatchBucket) =>
    apiFetch<{ match_id: string; bucket: MatchBucket; owner_email: string }>(
      `/api/matches/${encodeURIComponent(matchId)}/bucket`,
      {
        method: "POST",
        body: JSON.stringify({ bucket }),
      },
    ),
  getSystemStatus: () => apiFetch<SystemStatusResponse>("/api/system/status"),
  switchProfile: (profile: ModelProfile) =>
    apiFetch<{ active_profile: ModelProfile }>("/api/system/profile", {
      method: "POST",
      body: JSON.stringify({ profile }),
    }),
  saveSystemConfig: (payload: {
    profile?: ModelProfile;
    ollama_base_url?: string;
    ollama_vision_model?: string;
    gemini_api_key?: string;
  }) =>
    apiFetch<{
      active_profile: ModelProfile;
      ollama_base_url: string;
      ollama_vision_model: string;
      gemini_key_configured: boolean;
    }>("/api/system/config", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  testConnection: (payload: {
    target: "local" | "cloud";
    ollama_base_url?: string;
    ollama_vision_model?: string;
    gemini_api_key?: string;
  }) =>
    apiFetch<ConnectionTestResult>("/api/system/test-connection", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  analyzeUpload: (file: File, profile: ModelProfile) => {
    const fd = new FormData();
    fd.append("file", file);
    fd.append("profile", profile);
    fd.append("sample_fps", "0.5");
    return apiFetch<Record<string, unknown>>("/api/analyze/upload", {
      method: "POST",
      body: fd,
    });
  },
  analyzeUrl: (url: string, profile: ModelProfile) =>
    apiFetch<Record<string, unknown>>("/api/analyze/url", {
      method: "POST",
      body: JSON.stringify({ url, profile, sample_fps: 0.5 }),
    }),
  generateBoard: (payload: {
    match_id?: string;
    video_path?: string;
    center_timestamp_sec: number;
    title: string;
    cues: string[];
  }) =>
    apiFetch<{ filename: string; output_path: string; url: string }>("/api/media/board", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  generateSlowmo: (payload: {
    match_id?: string;
    video_path?: string;
    start_sec: number;
    duration_sec: number;
    speed?: number;
  }) =>
    apiFetch<{ filename: string; output_path: string; url: string }>("/api/media/slowmo", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
};
