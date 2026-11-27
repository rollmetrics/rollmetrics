import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, getStoredToken, mapAdjudicatedSnapshotToMatch, setStoredToken, withAuthUrl } from "./api";
import { EmbeddedReferenceVideo } from "./components/EmbeddedReferenceVideo";
import { LandingPage } from "./components/LandingPage";
import type {
  AcademyProfile,
  AcademyReference,
  AdminOverviewResponse,
  AuthConfig,
  AuthUser,
  BeltRank,
  ConnectionTestResult,
  FilmStudySummary,
  LocalHardwareStatus,
  LocalVideoFile,
  MatchBucket,
  ModelProfile,
  PersonalCoachingSummary,
  ShowcaseEvent,
  ShowcaseMatch,
  SystemStatusResponse,
} from "./types";

declare global {
  interface Window {
    google?: {
      accounts?: {
        id?: {
          initialize: (cfg: {
            client_id: string;
            callback: (resp: { credential?: string }) => void;
          }) => void;
          renderButton: (el: HTMLElement, opts: Record<string, unknown>) => void;
          disableAutoSelect?: () => void;
        };
      };
    };
  }
}

const FALLBACK_SEMIFINAL: ShowcaseMatch = {
  id: "mc9_semifinal",
  pill: "Mathewson vs. Dube",
  title: "Mastermind Cup 9 · Semifinal",
  belt: "white",
  belt_label: "White Belt · Gi · Master",
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
      cta_url: "https://www.youtube.com/watch?v=4-7AYrBCOvQ&t=4797s",
      pts_a: 0,
      adv_a: 0,
      pen_a: 0,
      pts_b: 0,
      adv_b: 0,
      pen_b: 1,
    },
    {
      id: "sf-3",
      video_sec: 170,
      time_str: "02:50",
      clock_str: "02:26",
      actor: "A",
      call: "OFF-BALANCE",
      detail: "Diamond Trap",
      rule: "IBJJF Art. 5.2",
      cta_url: "https://youtu.be/KCJ54kkfoGA?t=15",
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
};

function parseClockStr(clockStr: string): number | null {
  const parts = clockStr.trim().split(":");
  if (parts.length !== 2) return null;
  const m = Number(parts[0]);
  const s = Number(parts[1]);
  if (!Number.isFinite(m) || !Number.isFinite(s)) return null;
  return m * 60 + s;
}

function interpolateRemainingClock(currentTime: number, match: ShowcaseMatch): number {
  const anchors: Array<[number, number]> =
    match.clock_anchors && match.clock_anchors.length >= 2
      ? match.clock_anchors
      : (() => {
          const pts: Array<[number, number]> = [[match.video_start_sec, match.regulation_sec]];
          for (const ev of match.events) {
            const parsed = parseClockStr(ev.clock_str);
            if (parsed !== null && ev.video_sec > pts[pts.length - 1][0]) {
              pts.push([ev.video_sec, parsed]);
            }
          }
          if (pts.length === 1) {
            const dur = Math.max(0, match.video_end_sec - match.video_start_sec);
            pts.push([match.video_end_sec, Math.max(0, match.regulation_sec - dur)]);
          }
          return pts;
        })();

  if (currentTime <= anchors[0][0]) return anchors[0][1];
  const last = anchors[anchors.length - 1];
  if (currentTime >= last[0]) return last[1];

  for (let i = 0; i < anchors.length - 1; i += 1) {
    const [t0, c0] = anchors[i];
    const [t1, c1] = anchors[i + 1];
    if (currentTime >= t0 && currentTime <= t1) {
      if (t1 <= t0) return c1;
      const ratio = (currentTime - t0) / (t1 - t0);
      return Math.max(0, c0 + ratio * (c1 - c0));
    }
  }
  return last[1];
}

function formatCountdown(seconds: number): string {
  const clamped = Math.max(0, Math.round(seconds));
  const m = Math.floor(clamped / 60);
  const s = clamped % 60;
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

function shortAthleteLabel(fullName: string, isAthleteA: boolean): string {
  const parts = fullName.trim().split(/\s+/);
  if (parts.length === 0) return isAthleteA ? "Athlete A" : "Athlete B";
  const first = parts[0];
  if (first === "Kory" || first === "Caio" || first === "Vitor") return first;
  return parts[parts.length - 1];
}

function formatCallPrefix(ev: ShowcaseEvent, actorLabel: string, isBoth: boolean): string {
  if (ev.call === "PEN #2 EACH") {
    return "2nd penalty each -> +1 Adv to opponent";
  }
  if (isBoth) {
    return "Simultaneous penalty (Both)";
  }
  if (ev.call.includes("DQ") || ev.call.includes("ILLEGAL")) {
    return `Disqualification on ${actorLabel}`;
  }
  if (ev.call.includes("PEN")) {
    return `Penalty on ${actorLabel}`;
  }
  if (ev.call.includes("PTS") || ev.call.includes("ADV")) {
    return `${ev.call} to ${actorLabel}`;
  }
  if (ev.call === "SUBMISSION" || ev.call === "DECISION" || ev.call.includes("GOLD")) {
    return `Won by ${actorLabel}`;
  }
  return actorLabel;
}

const PENALTY_STEPS = [
  { step: 1, label: "1 Warning" },
  { step: 2, label: "2 +1 Adv to opp" },
  { step: 3, label: "3 +2 Pts to opp" },
  { step: 4, label: "4 DQ" },
];

function getInitials(name: string): string {
  const tokens = name.trim().split(/\s+/).filter(Boolean);
  if (tokens.length === 0) return "KM";
  if (tokens.length === 1) return tokens[0].slice(0, 2).toUpperCase();
  return `${tokens[0][0]}${tokens[tokens.length - 1][0]}`.toUpperCase();
}

function BeltSwatch({ belt }: { belt: BeltRank }) {
  return (
    <span className={`belt-swatch ${belt}`} aria-hidden="true">
      <span className="belt-tip" />
    </span>
  );
}

function extractVideoStartSec(url?: string, explicitSec?: number): number | undefined {
  if (typeof explicitSec === "number" && explicitSec > 0) return Math.floor(explicitSec);
  if (!url) return undefined;
  const m = url.match(/[?&#](?:t|start)=(\d+)/i);
  if (!m) return undefined;
  const val = Number.parseInt(m[1], 10);
  return Number.isFinite(val) && val > 0 ? val : undefined;
}

function formatVideoTimestamp(url?: string, explicitSec?: number): string {
  const sec = extractVideoStartSec(url, explicitSec);
  if (!sec || sec <= 0) return "";
  const hrs = Math.floor(sec / 3600);
  const mins = Math.floor((sec % 3600) / 60);
  const rem = sec % 60;
  if (hrs > 0) {
    return `${hrs}:${String(mins).padStart(2, "0")}:${String(rem).padStart(2, "0")}`;
  }
  return `${mins}:${String(rem).padStart(2, "0")}`;
}

function callToneClass(call: string): string {
  if (call.includes("PEN") || call.includes("DQ") || call.includes("ILLEGAL")) return "pen";
  if (call.includes("ADV")) return "adv";
  if (
    call.includes("SUBMISSION") ||
    call.includes("PTS") ||
    call.includes("DECISION") ||
    call.includes("GOLD")
  ) {
    return "win";
  }
  return "";
}

function mapLiveTimelineToEvents(timeline: Array<Record<string, unknown>>): ShowcaseEvent[] {
  let ptsA = 0;
  let advA = 0;
  let penA = 0;
  let ptsB = 0;
  let advB = 0;
  let penB = 0;

  return timeline.map((ev, idx) => {
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
    const ts = Number(ev.timestamp_sec || 0);
    const recs = (ev.cta_recommendations as Array<Record<string, unknown>>) || [];

    return {
      id: String(ev.event_id || `live-${idx + 1}`),
      video_sec: ts,
      time_str: formatCountdown(ts),
      clock_str: String(ev.match_clock_str || formatCountdown(Math.max(0, 300 - ts))),
      actor,
      call,
      detail: String(ev.position_after || rawKind.replace(/_/g, " ")),
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
}

function compactDivisionLabel(fullLabel: string): string {
  const parts = fullLabel.split("·").map((p) => p.trim());
  const beltWord = (parts[0] || "White").replace(/\s+Belt$/i, "");
  const ruleWord = parts[1] || "Gi";
  return `${beltWord} · ${ruleWord}`;
}

function compactWinBadge(winLabel: string): string {
  const upper = winLabel.trim().toUpperCase();
  if (upper.includes("SUB")) return "SUB";
  if (upper.includes("DQ")) return "DQ";
  if (upper.includes("DEC")) return "DEC";
  return "WIN";
}

function formatAuthGateError(raw: string): string {
  const lower = raw.toLowerCase();
  if (lower.includes("allowlist") || lower.includes("403") || lower.includes("outside the authorized")) {
    return "This account doesn't have access.";
  }
  return "Sign-in failed. Please try again.";
}

function slugifyName(name: string): string {
  return name
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "");
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

function extractShareTokenFromPath(pathname: string): string {
  const m = pathname.match(/^\/share\/([^/]+)\/?$/);
  return m ? decodeURIComponent(m[1]) : "";
}

export function App() {
  const [authConfig, setAuthConfig] = useState<AuthConfig | null>(null);
  const [user, setUser] = useState<AuthUser | null>(null);
  const [authError, setAuthError] = useState<string>("");

  const [shareToken, setShareToken] = useState<string>(() =>
    typeof window !== "undefined" ? extractShareTokenFromPath(window.location.pathname) : "",
  );
  const [sharedMatch, setSharedMatch] = useState<ShowcaseMatch | null>(null);
  const [sharedAcademyName, setSharedAcademyName] = useState<string>("Academy Library");
  const [shareLoadError, setShareLoadError] = useState<string>("");
  const [shareCopiedMsg, setShareCopiedMsg] = useState<string>("");

  const [uploadModalOpen, setUploadModalOpen] = useState<boolean>(false);
  const [uploadTab, setUploadTab] = useState<"file" | "drive">("file");
  const [uploadUniform, setUploadUniform] = useState<"gi" | "nogi">("gi");
  const [uploadBelt, setUploadBelt] = useState<BeltRank>("white");
  const [uploadAthleteA, setUploadAthleteA] = useState<string>("");
  const [uploadAthleteB, setUploadAthleteB] = useState<string>("");
  const [uploadGiA, setUploadGiA] = useState<"white" | "blue" | "black">("white");
  const [uploadGiB, setUploadGiB] = useState<"white" | "blue" | "black">("blue");
  const [uploadTitle, setUploadTitle] = useState<string>("");
  const [driveUrlInput, setDriveUrlInput] = useState<string>("");
  const [uploadProgress, setUploadProgress] = useState<number>(0);
  const [uploadError, setUploadError] = useState<string>("");

  const [matches, setMatches] = useState<ShowcaseMatch[]>([FALLBACK_SEMIFINAL]);
  const [activeMatchId, setActiveMatchId] = useState<string>("mc9_semifinal");
  const [currentTime, setCurrentTime] = useState<number>(0);
  const [videoDuration, setVideoDuration] = useState<number>(0);
  const [isPlaying, setIsPlaying] = useState<boolean>(false);
  const [isMuted, setIsMuted] = useState<boolean>(false);
  const [videoBuffering, setVideoBuffering] = useState<boolean>(false);
  const [videoError, setVideoError] = useState<boolean>(false);
  const [videoRetryKey, setVideoRetryKey] = useState<number>(0);
  const [hoveredEventId, setHoveredEventId] = useState<string | null>(null);
  const [selectedEventId, setSelectedEventId] = useState<string>("sf-4");
  const [rulePeekEventId, setRulePeekEventId] = useState<string | null>(null);
  const [ctaPeekEventId, setCtaPeekEventId] = useState<string | null>(null);
  const [ruleVideoOpenEventId, setRuleVideoOpenEventId] = useState<string | null>(null);
  const [coachOpenLinkKey, setCoachOpenLinkKey] = useState<string | null>(null);
  const [referenceModal, setReferenceModal] = useState<{
    title: string;
    subtitle?: string;
    url: string;
    startSec?: number;
  } | null>(null);
  const [profile, setProfile] = useState<ModelProfile>("local");

  const [drawerOpen, setDrawerOpen] = useState<boolean>(false);
  const [settingsOpen, setSettingsOpen] = useState<boolean>(false);
  const [coachExpanded, setCoachExpanded] = useState<boolean>(false);
  const [inlineGifByEvent, setInlineGifByEvent] = useState<
    Record<string, { url: string; label: string }>
  >({});
  const [inlineGifOpenId, setInlineGifOpenId] = useState<string | null>(null);
  const [demoLoginBusy, setDemoLoginBusy] = useState<boolean>(false);
  const [previewAsUser, setPreviewAsUser] = useState<boolean>(false);
  const [viewMode, setViewMode] = useState<
    "player" | "home" | "admin" | "ml-admin" | "privacy" | "terms"
  >(() => {
    if (typeof window === "undefined") return "player";
    const p = window.location.pathname;
    if (p === "/admin") return "admin";
    if (p === "/ml" || p === "/ml-admin") return "ml-admin";
    if (p === "/privacy") return "privacy";
    if (p === "/terms") return "terms";
    return "player";
  });
  const [filmStudyAthlete, setFilmStudyAthlete] = useState<string>("");
  const [filmStudySummary, setFilmStudySummary] = useState<FilmStudySummary | null>(null);
  const [adminOverview, setAdminOverview] = useState<AdminOverviewResponse | null>(null);
  const [adminSplit, setAdminSplit] = useState<"heldout" | "calibration">("heldout");
  const [adminLoading, setAdminLoading] = useState<boolean>(false);
  const [uploadBucket, setUploadBucket] = useState<MatchBucket>("my_matches");
  const [personalSummary, setPersonalSummary] = useState<PersonalCoachingSummary | null>(null);
  const [sysStatus, setSysStatus] = useState<SystemStatusResponse | null>(null);
  const [ollamaUrlInput, setOllamaUrlInput] = useState<string>("http://127.0.0.1:11434");
  const [ollamaModelInput, setOllamaModelInput] = useState<string>("gemma4:e2b-it-q4_K_M");
  const [geminiKeyInput, setGeminiKeyInput] = useState<string>("");
  const [localTestRes, setLocalTestRes] = useState<ConnectionTestResult | null>(null);
  const [cloudTestRes, setCloudTestRes] = useState<ConnectionTestResult | null>(null);
  const [testingTarget, setTestingTarget] = useState<"local" | "cloud" | null>(null);
  const [configSavedMsg, setConfigSavedMsg] = useState<string>("");
  const [academy, setAcademy] = useState<AcademyProfile | null>(null);
  const [academyRefs, setAcademyRefs] = useState<AcademyReference[]>([]);
  const [academyNameInput, setAcademyNameInput] = useState<string>("");
  const [refTitleInput, setRefTitleInput] = useState<string>("");
  const [refPositionInput, setRefPositionInput] = useState<string>("closed_guard");
  const [refUrlInput, setRefUrlInput] = useState<string>("");
  const [refNotesInput, setRefNotesInput] = useState<string>("");
  const [academyMsg, setAcademyMsg] = useState<string>("");
  const [localHardware, setLocalHardware] = useState<LocalHardwareStatus | null>(null);
  const [localFolderInput, setLocalFolderInput] = useState<string>("~/Videos/BJJ");
  const [localVideos, setLocalVideos] = useState<LocalVideoFile[]>([]);
  const [localNotesFolderInput, setLocalNotesFolderInput] = useState<string>("");
  const [localDesktopBusy, setLocalDesktopBusy] = useState<boolean>(false);
  const [localDesktopMsg, setLocalDesktopMsg] = useState<string>("");

  const [urlInput, setUrlInput] = useState<string>("");
  const [ingestBusy, setIngestBusy] = useState<boolean>(false);
  const [liveCameraActive, setLiveCameraActive] = useState<boolean>(false);
  const [liveSimActive, setLiveSimActive] = useState<boolean>(false);
  const [liveSimBlobUrl, setLiveSimBlobUrl] = useState<string | null>(null);
  const [simMatchId, setSimMatchId] = useState<string>("mc9_semifinal");
  const [liveScore, setLiveScore] = useState<{
    ptsA: number;
    advA: number;
    penA: number;
    ptsB: number;
    advB: number;
    penB: number;
    stabKind: string | null;
    stabHeldSec: number;
    finished: boolean;
  }>({
    ptsA: 0,
    advA: 0,
    penA: 0,
    ptsB: 0,
    advB: 0,
    penB: 0,
    stabKind: null,
    stabHeldSec: 0,
    finished: false,
  });
  const [liveEvents, setLiveEvents] = useState<ShowcaseEvent[]>([]);
  const [liveTelemetry, setLiveTelemetry] = useState<{
    latencyMs: number | null;
    modelId: string;
    framesSent: number;
    position: string;
  }>({
    latencyMs: null,
    modelId: "",
    framesSent: 0,
    position: "Standing",
  });

  const [mediaModal, setMediaModal] = useState<{ url: string; label: string } | null>(null);
  const [mediaBusy, setMediaBusy] = useState<boolean>(false);

  const videoFrameRef = useRef<HTMLDivElement | null>(null);
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const scrubberRailRef = useRef<HTMLDivElement | null>(null);
  const scrubbingRef = useRef<boolean>(false);
  const liveStreamRef = useRef<MediaStream | null>(null);
  const liveWsRef = useRef<WebSocket | null>(null);
  const liveLoopTimerRef = useRef<number | null>(null);
  const liveInFlightRef = useRef<boolean>(false);
  const liveInFlightSinceRef = useRef<number>(0);
  const liveLastSentRef = useRef<number>(0);
  const liveCanvasRef = useRef<HTMLCanvasElement | null>(null);
  const gisButtonRef = useRef<HTMLDivElement | null>(null);
  const gisInitializedClientRef = useRef<string>("");

  const loadWorkspace = useCallback(async () => {
    try {
      const me = await api.getMe();
      const catalog = await api.getMatches();
      setUser(me);
      if (catalog.matches.length > 0) {
        setMatches(catalog.matches);
        const defId = catalog.default_match_id || catalog.matches[0].id;
        setActiveMatchId(defId);
        const defMatch = catalog.matches.find((m) => m.id === defId) || catalog.matches[0];
        if (defMatch.events.length > 0) {
          setSelectedEventId(defMatch.events[defMatch.events.length - 1].id);
        }
      }
      api
        .getPersonalCoachingSummary()
        .then((gp) => setPersonalSummary(gp))
        .catch(() => {});
      api
        .getMyAcademy()
        .then((ac) => {
          if (ac?.academy_name) {
            setAcademy(ac);
            setAcademyNameInput(ac.academy_name);
          }
        })
        .catch(() => {
          const fallbackName = me.academy_name || "Academy Library";
          setAcademy({
            academy_id: me.academy_id || "default",
            academy_name: fallbackName,
          });
          setAcademyNameInput(fallbackName);
        });
      setAuthError("");
    } catch (err) {
      setStoredToken("");
      setUser(null);
      setAuthError((err as Error).message);
    }
  }, []);

  useEffect(() => {
    api
      .getAuthConfig()
      .then((cfg) => {
        setAuthConfig(cfg);
        setProfile(cfg.default_profile || "local");
        if (!cfg.auth_required || getStoredToken()) {
          void loadWorkspace();
        }
      })
      .catch((err) => {
        setAuthConfig({
          environment: "local",
          google_client_id: "",
          allow_local_dev_bypass: true,
          auth_required: false,
          public_url: typeof window !== "undefined" ? window.location.origin : "http://127.0.0.1:8090",
          default_profile: "local",
        });
        setAuthError((err as Error).message);
      });
  }, [loadWorkspace]);

  useEffect(() => {
    if (!authConfig?.auth_required || user || !authConfig.google_client_id) return;
    let cancelled = false;
    let attempts = 0;

    const tryMountGis = (): boolean => {
      if (cancelled || !window.google?.accounts?.id || !gisButtonRef.current) return false;
      if (gisInitializedClientRef.current !== authConfig.google_client_id) {
        window.google.accounts.id.initialize({
          client_id: authConfig.google_client_id,
          callback: (resp) => {
            if (resp.credential) {
              setStoredToken(resp.credential);
              void loadWorkspace();
            }
          },
        });
        gisInitializedClientRef.current = authConfig.google_client_id;
      }
      if (gisButtonRef.current.childElementCount === 0) {
        window.google.accounts.id.renderButton(gisButtonRef.current, {
          theme: "filled_black",
          size: "large",
          shape: "pill",
        });
      }
      return true;
    };

    if (tryMountGis()) return;
    const timer = window.setInterval(() => {
      attempts += 1;
      if (tryMountGis() || attempts >= 25) {
        window.clearInterval(timer);
      }
    }, 150);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [authConfig, loadWorkspace, user]);

  const navigateViewMode = useCallback(
    (next: "player" | "home" | "admin" | "ml-admin" | "privacy" | "terms") => {
      setViewMode(next);
      if (typeof window !== "undefined" && typeof window.history?.pushState === "function") {
        const targetPath =
          next === "admin"
            ? "/admin"
            : next === "ml-admin"
              ? "/ml"
              : next === "privacy"
                ? "/privacy"
                : next === "terms"
                  ? "/terms"
                  : "/";
        if (window.location.pathname !== targetPath) {
          window.history.pushState({ viewMode: next }, "", targetPath);
        }
      }
    },
    [],
  );

  useEffect(() => {
    if (!shareToken) {
      setSharedMatch(null);
      setShareLoadError("");
      return;
    }
    let cancelled = false;
    api
      .getSharedMatch(shareToken)
      .then((res) => {
        if (cancelled) return;
        setSharedMatch(res.match);
        setSharedAcademyName(res.academy_name || "Academy Library");
        if (res.match.events.length > 0) {
          setSelectedEventId(res.match.events[0].id);
        }
        setShareLoadError("");
      })
      .catch((err) => {
        if (!cancelled) setShareLoadError((err as Error).message || "Shared match not found.");
      });
    return () => {
      cancelled = true;
    };
  }, [shareToken]);

  useEffect(() => {
    const onPopState = () => {
      const p = window.location.pathname;
      const nextShare = extractShareTokenFromPath(p);
      setShareToken(nextShare);
      if (nextShare) return;
      if (p === "/admin") {
        setViewMode("admin");
      } else if (p === "/ml" || p === "/ml-admin") {
        setViewMode("ml-admin");
      } else if (p === "/privacy") {
        setViewMode("privacy");
      } else if (p === "/terms") {
        setViewMode("terms");
      } else {
        setViewMode((prev) =>
          prev === "admin" || prev === "ml-admin" || prev === "privacy" || prev === "terms"
            ? "player"
            : prev,
        );
      }
    };
    window.addEventListener("popstate", onPopState);
    return () => window.removeEventListener("popstate", onPopState);
  }, []);

  useEffect(() => {
    if ((viewMode !== "admin" && viewMode !== "ml-admin") || !user) return;
    let cancelled = false;
    setAdminLoading(true);
    api
      .getAdminOverview()
      .then((data) => {
        if (!cancelled) setAdminOverview(data);
      })
      .catch(() => {})
      .finally(() => {
        if (!cancelled) setAdminLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [viewMode, user]);

  useEffect(() => {
    if (!filmStudyAthlete || !user) {
      setFilmStudySummary(null);
      return;
    }
    let cancelled = false;
    api
      .getFilmStudySummary(filmStudyAthlete)
      .then((summary) => {
        if (!cancelled) setFilmStudySummary(summary);
      })
      .catch(() => {
        if (!cancelled) setFilmStudySummary(null);
      });
    return () => {
      cancelled = true;
    };
  }, [filmStudyAthlete, user]);

  const filmStudyMatches = useMemo(
    () => matches.filter((m) => m.bucket !== "my_matches"),
    [matches],
  );

  const filmStudyAthletes = useMemo(() => {
    const counts = new Map<string, number>();
    for (const m of filmStudyMatches) {
      const primary = m.athlete_a.name.trim();
      if (primary) {
        counts.set(primary, (counts.get(primary) || 0) + 1);
      }
    }
    return Array.from(counts.entries())
      .map(([name, count]) => ({ name, count }))
      .sort((a, b) => b.count - a.count || a.name.localeCompare(b.name));
  }, [filmStudyMatches]);

  const filteredFilmStudyMatches = useMemo(() => {
    if (!filmStudyAthlete) return filmStudyMatches;
    return filmStudyMatches.filter(
      (m) => m.athlete_a.name === filmStudyAthlete || m.athlete_b.name === filmStudyAthlete,
    );
  }, [filmStudyAthlete, filmStudyMatches]);

  const activeMatch = useMemo(
    () =>
      shareToken && sharedMatch
        ? sharedMatch
        : matches.find((m) => m.id === activeMatchId) || matches[0] || FALLBACK_SEMIFINAL,
    [activeMatchId, matches, shareToken, sharedMatch],
  );

  const labelA = useMemo(() => shortAthleteLabel(activeMatch.athlete_a.name, true), [activeMatch]);
  const labelB = useMemo(() => shortAthleteLabel(activeMatch.athlete_b.name, false), [activeMatch]);

  // Derive live IBJJF scoreboard state synchronized with video.currentTime
  const syncedState = useMemo(() => {
    const passed = activeMatch.events.filter((ev) => currentTime >= ev.video_sec - 0.25);
    const latestPassed = passed.length > 0 ? passed[passed.length - 1] : null;
    const remainingSec = interpolateRemainingClock(currentTime, activeMatch);

    const stabilizingNow = activeMatch.events.some(
      (ev) => ev.control_3s && currentTime >= ev.video_sec - 3.0 && currentTime <= ev.video_sec + 1.5,
    );

    return {
      ptsA: latestPassed ? latestPassed.pts_a : 0,
      advA: latestPassed ? latestPassed.adv_a : 0,
      penA: latestPassed ? latestPassed.pen_a : 0,
      ptsB: latestPassed ? latestPassed.pts_b : 0,
      advB: latestPassed ? latestPassed.adv_b : 0,
      penB: latestPassed ? latestPassed.pen_b : 0,
      finished: Boolean(latestPassed?.is_finish),
      clock: latestPassed?.is_finish ? latestPassed.clock_str : formatCountdown(remainingSec),
      stabilizingNow,
      activeByTimeId: latestPassed?.id || null,
    };
  }, [activeMatch, currentTime]);

  const highlightedEventId = syncedState.activeByTimeId || selectedEventId || activeMatch.events[0]?.id || "";

  const effectiveDuration =
    videoDuration > 0 ? videoDuration : Math.max(1, activeMatch.video_duration_sec);

  const togglePlayPause = useCallback(() => {
    const v = videoRef.current;
    if (!v) return;
    if (v.paused) {
      const playPromise = v.play();
      if (playPromise && typeof playPromise.catch === "function") {
        void playPromise.catch(() => {
          v.muted = true;
          setIsMuted(true);
          const retry = v.play();
          if (retry && typeof retry.catch === "function") {
            void retry.catch(() => {});
          }
        });
      }
    } else {
      v.pause();
    }
  }, []);

  const toggleMute = () => {
    const v = videoRef.current;
    if (!v) return;
    v.muted = !v.muted;
    setIsMuted(v.muted);
  };

  const toggleFullscreen = () => {
    const frame = videoFrameRef.current;
    const v = videoRef.current as
      | (HTMLVideoElement & {
          webkitEnterFullscreen?: () => void;
          webkitRequestFullscreen?: () => Promise<void> | void;
        })
      | null;
    if (document.fullscreenElement) {
      void document.exitFullscreen?.();
      return;
    }
    if (v && typeof v.webkitEnterFullscreen === "function") {
      v.webkitEnterFullscreen();
      return;
    }
    if (frame && typeof frame.requestFullscreen === "function") {
      void frame.requestFullscreen();
      return;
    }
    if (v && typeof v.webkitRequestFullscreen === "function") {
      void v.webkitRequestFullscreen();
    }
  };

  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.code !== "Space") return;
      const tag = (e.target as HTMLElement | null)?.tagName?.toLowerCase();
      if (tag === "input" || tag === "textarea" || tag === "select" || tag === "button") return;
      e.preventDefault();
      togglePlayPause();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [togglePlayPause]);

  const stopLiveSession = useCallback(() => {
    if (liveLoopTimerRef.current !== null) {
      window.clearInterval(liveLoopTimerRef.current);
      liveLoopTimerRef.current = null;
    }
    if (liveWsRef.current) {
      try {
        liveWsRef.current.close();
      } catch {
        // Ignore close errors
      }
      liveWsRef.current = null;
    }
    if (liveStreamRef.current) {
      liveStreamRef.current.getTracks().forEach((t) => t.stop());
      liveStreamRef.current = null;
    }
    if (videoRef.current) {
      videoRef.current.srcObject = null;
      if (!videoRef.current.paused) {
        try {
          videoRef.current.pause();
        } catch {
          // Ignore test environment pause
        }
      }
    }
    if (liveSimBlobUrl) {
      URL.revokeObjectURL(liveSimBlobUrl);
      setLiveSimBlobUrl(null);
    }
    liveInFlightRef.current = false;
    setLiveCameraActive(false);
    setLiveSimActive(false);
  }, [liveSimBlobUrl]);

  useEffect(() => {
    return () => {
      if (liveLoopTimerRef.current !== null) window.clearInterval(liveLoopTimerRef.current);
      liveWsRef.current?.close();
    };
  }, []);

  const handleSelectMatch = (matchId: string) => {
    if (liveCameraActive || liveSimActive) {
      stopLiveSession();
    }
    navigateViewMode("player");
    setActiveMatchId(matchId);
    setSimMatchId(matchId);
    setCurrentTime(0);
    setVideoDuration(0);
    setIsPlaying(false);
    setVideoBuffering(false);
    setVideoError(false);
    setHoveredEventId(null);
    setRulePeekEventId(null);
    const target = matches.find((m) => m.id === matchId);
    if (target && target.events.length > 0) {
      setSelectedEventId(target.events[0].id);
    }
  };

  const handleSeekToEvent = (ev: ShowcaseEvent) => {
    setSelectedEventId(ev.id);
    setCurrentTime(ev.video_sec);
    if (videoRef.current && !liveCameraActive) {
      videoRef.current.currentTime = ev.video_sec;
    }
  };

  const seekScrubberAtClientX = (clientX: number) => {
    const rail = scrubberRailRef.current;
    if (!rail || liveCameraActive) return;
    const rect = rail.getBoundingClientRect();
    if (rect.width <= 0) return;
    const ratio = Math.max(0, Math.min(1, (clientX - rect.left) / rect.width));
    const nextSec = ratio * effectiveDuration;
    setCurrentTime(nextSec);
    if (videoRef.current) {
      videoRef.current.currentTime = nextSec;
    }
  };

  const handleOpenSettings = async () => {
    setSettingsOpen(true);
    setConfigSavedMsg("");
    setAcademyMsg("");
    try {
      const st = await api.getSystemStatus();
      setSysStatus(st);
      setProfile(st.active_profile || profile);
      if (st.ollama_base_url) setOllamaUrlInput(st.ollama_base_url);
      const recModel = st.providers?.recommended_local_model || st.models?.ollama_vision_model;
      if (recModel) setOllamaModelInput(recModel);
    } catch {
      // Keep local defaults if offline
    }
    api
      .getMyAcademy()
      .then((ac) => {
        if (ac?.academy_name) {
          setAcademy(ac);
          setAcademyNameInput(ac.academy_name);
        }
      })
      .catch(() => {});
    api
      .getMyAcademyReferences()
      .then((res) => setAcademyRefs(Array.isArray(res?.references) ? res.references : []))
      .catch(() => {});
    api
      .getLocalHardware()
      .then((hw) => setLocalHardware(hw))
      .catch(() => {});
  };

  const handleScanLocalFolder = async () => {
    if (!localFolderInput.trim()) return;
    setLocalDesktopBusy(true);
    setLocalDesktopMsg("");
    try {
      const res = await api.scanLocalFolder(localFolderInput.trim());
      setLocalVideos(res.videos || []);
      setLocalDesktopMsg(`Found ${res.count} local video${res.count === 1 ? "" : "s"}.`);
    } catch (err) {
      setLocalDesktopMsg((err as Error).message || "Folder scan failed.");
    } finally {
      setLocalDesktopBusy(false);
    }
  };

  const handleAnalyzeLocalFile = async (vid: LocalVideoFile) => {
    setLocalDesktopBusy(true);
    setLocalDesktopMsg(`Analyzing ${vid.filename} offline...`);
    try {
      const res = await api.analyzeLocalVideo({
        video_path: vid.path,
        title: vid.filename.replace(/\.[^.]+$/, ""),
        bucket: "my_matches",
      });
      setMatches((prev) => [res.match, ...prev]);
      setActiveMatchId(res.match.id);
      setSettingsOpen(false);
      navigateViewMode("player");
    } catch (err) {
      setLocalDesktopMsg((err as Error).message || "Offline analysis failed.");
    } finally {
      setLocalDesktopBusy(false);
    }
  };

  const handleImportLocalAcademyFolder = async () => {
    if (!localNotesFolderInput.trim()) return;
    setLocalDesktopBusy(true);
    setLocalDesktopMsg("");
    try {
      const res = await api.importLocalAcademyFolder({
        folder_path: localNotesFolderInput.trim(),
        academy_id: academy?.academy_id,
        academy_name: academy?.academy_name,
      });
      setLocalDesktopMsg(`Imported ${res.imported_count} note(s) into ${res.academy_name}.`);
      setLocalNotesFolderInput("");
      const refreshed = await api.getMyAcademyReferences().catch(() => null);
      if (refreshed?.references) setAcademyRefs(refreshed.references);
    } catch (err) {
      setLocalDesktopMsg((err as Error).message || "Folder import failed.");
    } finally {
      setLocalDesktopBusy(false);
    }
  };

  const handleSaveAcademy = async () => {
    const trimmed = academyNameInput.trim();
    if (!trimmed) return;
    try {
      const updated = await api.updateMyAcademy({ academy_name: trimmed });
      setAcademy(updated);
      setAcademyNameInput(updated.academy_name || trimmed);
      setAcademyMsg(`Saved academy: ${updated.academy_name || trimmed}`);
    } catch (err) {
      setAcademyMsg((err as Error).message);
    }
  };

  const handleAddAcademyRef = async () => {
    const title = refTitleInput.trim();
    if (!title) return;
    try {
      const res = await api.addMyAcademyReference({
        title,
        position: refPositionInput.trim() || "all",
        source_url: refUrlInput.trim() || undefined,
        notes: refNotesInput.trim() || undefined,
      });
      const added: AcademyReference = res?.reference || {
        id: `ref-${Date.now()}`,
        title,
        position: refPositionInput.trim() || "all",
        source_url: refUrlInput.trim(),
        notes: refNotesInput.trim(),
      };
      setAcademyRefs((prev) => [added, ...prev]);
      setRefTitleInput("");
      setRefUrlInput("");
      setRefNotesInput("");
      setAcademyMsg(`Added "${title}" to ${academy?.academy_name || "Academy Library"}.`);
    } catch (err) {
      setAcademyMsg((err as Error).message);
    }
  };

  const handleSaveConfig = async (nextProfile?: ModelProfile) => {
    const chosen = nextProfile || profile;
    setProfile(chosen);
    try {
      const saved = await api.saveSystemConfig({
        profile: chosen,
        ollama_base_url: ollamaUrlInput,
        ollama_vision_model: ollamaModelInput,
        gemini_api_key: geminiKeyInput.trim() || undefined,
      });
      setProfile(saved.active_profile);
      setGeminiKeyInput("");
      setConfigSavedMsg(`Saved (${saved.active_profile === "local" ? "Local Ollama" : "Cloud Gemini 4"} active).`);
      const st = await api.getSystemStatus();
      setSysStatus(st);
    } catch (err) {
      setConfigSavedMsg((err as Error).message);
    }
  };

  const handleTestConnection = async (target: "local" | "cloud") => {
    setTestingTarget(target);
    try {
      const res = await api.testConnection({
        target,
        ollama_base_url: ollamaUrlInput,
        ollama_vision_model: ollamaModelInput,
        gemini_api_key: geminiKeyInput.trim() || undefined,
      });
      if (target === "local") {
        setLocalTestRes(res);
        if (res.model) setOllamaModelInput(res.model);
      } else {
        setCloudTestRes(res);
      }
    } finally {
      setTestingTarget(null);
    }
  };

  const handleRenderHighlight = async (ev: ShowcaseEvent, num: number) => {
    if (inlineGifOpenId === ev.id) {
      setInlineGifOpenId(null);
      return;
    }
    if (inlineGifByEvent[ev.id]) {
      setInlineGifOpenId(ev.id);
      return;
    }
    setMediaBusy(true);
    try {
      const actorName = ev.actor === "B" ? labelB : labelA;
      const res = shareToken
        ? await api.generateSharedHighlight(shareToken, {
            start_sec: Math.max(0, ev.video_sec - 3.0),
            duration_sec: 5.0,
            speed: 0.4,
          })
        : await api.generateSlowmo({
            match_id: activeMatch.video_path ? undefined : activeMatch.id,
            video_path: activeMatch.video_path,
            start_sec: Math.max(0, ev.video_sec - 3.0),
            duration_sec: 5.0,
            speed: 0.4,
          });
      const clip = {
        url: shareToken ? res.url : withAuthUrl(res.url),
        label: `#${num} · ${actorName} · 0.4x GIF (${ev.detail})`,
      };
      setInlineGifByEvent((prev) => ({ ...prev, [ev.id]: clip }));
      setInlineGifOpenId(ev.id);
    } finally {
      setMediaBusy(false);
    }
  };

  const handleDemoLogin = async () => {
    setDemoLoginBusy(true);
    setAuthError("");
    try {
      const res = await api.demoLogin();
      if (res.token) {
        setStoredToken(res.token);
      }
      setUser(res.user);
      setPreviewAsUser(false);
      await loadWorkspace();
      navigateViewMode("home");
    } catch (err) {
      setAuthError((err as Error).message || "Demo sign-in failed.");
    } finally {
      setDemoLoginBusy(false);
    }
  };

  const handleShareMatch = async () => {
    try {
      const res = await api.createShareLink(activeMatch.id);
      const urlToCopy =
        typeof window !== "undefined" && window.location.origin.startsWith("http")
          ? `${window.location.origin}${res.share_path}`
          : res.share_url;
      await navigator.clipboard?.writeText?.(urlToCopy).catch(() => {});
      setShareCopiedMsg("Link copied");
      window.setTimeout(() => setShareCopiedMsg(""), 2500);
    } catch {
      setShareCopiedMsg("Share failed");
      window.setTimeout(() => setShareCopiedMsg(""), 2500);
    }
  };

  const handleDirectUploadFile = async (file: File | null) => {
    if (!file) return;
    setIngestBusy(true);
    setUploadError("");
    setUploadProgress(15);
    try {
      const athA = uploadAthleteA.trim() || user?.name || "Athlete A";
      const athB = uploadAthleteB.trim() || "Opponent";
      const init = await api.initUpload({
        filename: file.name,
        content_type: file.type || "video/mp4",
        size_bytes: file.size,
        bucket: uploadBucket,
        uniform: uploadUniform,
        belt: uploadBelt,
        athlete_a_name: athA,
        athlete_b_name: athB,
        athlete_a_gi: uploadGiA,
        athlete_b_gi: uploadGiB,
        title: uploadTitle.trim() || file.name.replace(/\.[^.]+$/, ""),
      });
      setUploadProgress(45);
      let newMatch: ShowcaseMatch;
      if (init.upload_strategy === "gcs_signed_put") {
        const putResp = await fetch(init.upload_url, {
          method: "PUT",
          headers: init.headers,
          body: file,
        });
        if (!putResp.ok) throw new Error(`Direct upload failed (${putResp.status})`);
        setUploadProgress(80);
        const done = await api.completeUpload({ upload_id: init.upload_id, profile });
        newMatch = done.match;
      } else {
        const res = await api.analyzeUpload(file, profile);
        newMatch = {
          ...mapAdjudicatedSnapshotToMatch(res, uploadTitle.trim() || file.name.replace(/\.[^.]+$/, "")),
          bucket: uploadBucket,
          owner_email: uploadBucket === "my_matches" ? user?.email || "" : "",
        };
      }
      setUploadProgress(100);
      setMatches((prev) => [newMatch, ...prev]);
      setActiveMatchId(newMatch.id);
      navigateViewMode("player");
      setUploadModalOpen(false);
      setUploadTitle("");
    } catch (err) {
      setUploadError((err as Error).message || "Upload failed.");
    } finally {
      setIngestBusy(false);
      setUploadProgress(0);
    }
  };

  const handleGoogleDriveImport = async () => {
    if (!driveUrlInput.trim()) return;
    setIngestBusy(true);
    setUploadError("");
    setUploadProgress(35);
    try {
      const athA = uploadAthleteA.trim() || user?.name || "Athlete A";
      const athB = uploadAthleteB.trim() || "Opponent";
      const done = await api.uploadGoogleDrive({
        drive_url: driveUrlInput.trim(),
        title: uploadTitle.trim() || `${athA} vs. ${athB}`,
        bucket: uploadBucket,
        uniform: uploadUniform,
        belt: uploadBelt,
        athlete_a_name: athA,
        athlete_b_name: athB,
        athlete_a_gi: uploadGiA,
        athlete_b_gi: uploadGiB,
        profile,
      });
      setUploadProgress(100);
      setMatches((prev) => [done.match, ...prev]);
      setActiveMatchId(done.match.id);
      navigateViewMode("player");
      setUploadModalOpen(false);
      setDriveUrlInput("");
      setUploadTitle("");
    } catch (err) {
      setUploadError((err as Error).message || "Google Drive import failed.");
    } finally {
      setIngestBusy(false);
      setUploadProgress(0);
    }
  };

  const handleUploadVideo = async (file: File | null) => {
    if (!file) return;
    setIngestBusy(true);
    try {
      const res = await api.analyzeUpload(file, profile);
      const customMatch: ShowcaseMatch = {
        ...mapAdjudicatedSnapshotToMatch(res, file.name.replace(/\.[^.]+$/, "")),
        bucket: uploadBucket,
        owner_email: uploadBucket === "my_matches" ? user?.email || "" : "",
      };
      setMatches((prev) => [...prev, customMatch]);
      setActiveMatchId(customMatch.id);
      setViewMode("player");
      setDrawerOpen(false);
    } finally {
      setIngestBusy(false);
    }
  };

  const handleAnalyzeUrl = async () => {
    if (!urlInput.trim()) return;
    setIngestBusy(true);
    try {
      const res = await api.analyzeUrl(urlInput.trim(), profile);
      const customMatch: ShowcaseMatch = {
        ...mapAdjudicatedSnapshotToMatch(res, "Analyzed Clip"),
        bucket: uploadBucket,
        owner_email: uploadBucket === "my_matches" ? user?.email || "" : "",
      };
      setMatches((prev) => [...prev, customMatch]);
      setActiveMatchId(customMatch.id);
      setViewMode("player");
      setUrlInput("");
      setDrawerOpen(false);
    } finally {
      setIngestBusy(false);
    }
  };

  const startLiveSim = (targetMatchId: string, localFile?: File | null) => {
    stopLiveSession();
    const chosen = matches.find((m) => m.id === targetMatchId) || activeMatch;
    if (!localFile) {
      setActiveMatchId(chosen.id);
    }
    if (localFile) {
      const blobUrl = URL.createObjectURL(localFile);
      setLiveSimBlobUrl(blobUrl);
    } else {
      setLiveSimBlobUrl(null);
    }

    setLiveSimActive(true);
    setLiveCameraActive(false);
    setCurrentTime(0);
    setLiveEvents([]);
    setLiveScore({
      ptsA: 0,
      advA: 0,
      penA: 0,
      ptsB: 0,
      advB: 0,
      penB: 0,
      stabKind: null,
      stabHeldSec: 0,
      finished: false,
    });
    setLiveTelemetry({
      latencyMs: null,
      modelId: profile === "cloud" ? "gemma-4-26b-a4b-it" : ollamaModelInput,
      framesSent: 0,
      position: "Standing",
    });
    setDrawerOpen(false);

    const wsProto = window.location.protocol === "https:" ? "wss:" : "ws:";
    const wsPath = withAuthUrl("/ws/live");
    const ws = new WebSocket(`${wsProto}//${window.location.host}${wsPath}`);
    liveWsRef.current = ws;
    liveInFlightRef.current = false;
    liveLastSentRef.current = 0;

    ws.onopen = () => {
      const isNogi = chosen.belt_label.toLowerCase().includes("no-gi");
      const isMaster = chosen.belt_label.toLowerCase().includes("master");
      ws.send(
        JSON.stringify({
          type: "init",
          profile,
          division: {
            uniform: isNogi ? "nogi" : "gi",
            belt: chosen.belt,
            age_division: isMaster ? "master" : "adult",
            duration_sec: chosen.regulation_sec,
            athlete_a_name: chosen.athlete_a.name,
            athlete_b_name: chosen.athlete_b.name,
            locked_athlete: "A",
          },
        }),
      );
    };

    ws.onmessage = (event) => {
      liveInFlightRef.current = false;
      try {
        const msg = JSON.parse(String(event.data || "{}")) as Record<string, unknown>;
        if (msg.type === "pong" || msg.type === "error") return;
        const sb = (msg.scoreboard as Record<string, unknown>) || msg;
        const a = (sb.athlete_a as Record<string, unknown>) || {};
        const b = (sb.athlete_b as Record<string, unknown>) || {};
        const stab = (msg.active_stabilization as Record<string, unknown> | null) || null;
        const timeline = (sb.timeline as Array<Record<string, unknown>>) || [];

        setLiveScore({
          ptsA: Number(a.points || 0),
          advA: Number(a.advantages || 0),
          penA: Number(a.penalties || 0),
          ptsB: Number(b.points || 0),
          advB: Number(b.advantages || 0),
          penB: Number(b.penalties || 0),
          stabKind: stab ? String(stab.kind || "") : null,
          stabHeldSec: stab ? Number(stab.held_sec || 0) : 0,
          finished: Boolean(sb.ended),
        });
        setLiveEvents(mapLiveTimelineToEvents(timeline));
        setLiveTelemetry((prev) => ({
          latencyMs:
            typeof msg.latency_ms === "number"
              ? msg.latency_ms
              : prev.latencyMs,
          modelId: String(msg.model_id || msg.model_used || prev.modelId || ""),
          framesSent: prev.framesSent,
          position: String(sb.current_position || prev.position || "Standing"),
        }));
      } catch {
        // Ignore malformed WS payloads
      }
    };

    ws.onclose = () => {
      liveInFlightRef.current = false;
    };

    window.setTimeout(() => {
      const v = videoRef.current;
      if (v) {
        v.currentTime = 0;
        v.playbackRate = 1.0;
        void v.play?.().catch(() => {});
      }
    }, 80);

    const captureAndSend = () => {
      const activeWs = liveWsRef.current;
      const v = videoRef.current;
      if (!activeWs || activeWs.readyState !== WebSocket.OPEN || !v) return;
      const now = Date.now();
      // Minimum 1.5s spacing + strict in-flight backpressure (max 6s timeout) (#107)
      if (now - liveLastSentRef.current < 1500) return;
      if (liveInFlightRef.current && now - liveInFlightSinceRef.current < 6000) return;

      let canvas = liveCanvasRef.current;
      if (!canvas) {
        canvas = document.createElement("canvas");
        canvas.width = 640;
        canvas.height = 360;
        liveCanvasRef.current = canvas;
      }
      const ctx = canvas.getContext("2d");
      if (!ctx) return;
      try {
        ctx.drawImage(v, 0, 0, canvas.width, canvas.height);
        const dataUrl = canvas.toDataURL("image/jpeg", 0.72);
        const b64 = dataUrl.includes(",") ? dataUrl.split(",", 2)[1] : dataUrl;
        liveInFlightRef.current = true;
        liveInFlightSinceRef.current = now;
        liveLastSentRef.current = now;
        setLiveTelemetry((prev) => ({ ...prev, framesSent: prev.framesSent + 1 }));
        activeWs.send(
          JSON.stringify({
            type: "frame",
            image_b64: b64,
            elapsed_sec: Number((v.currentTime || 0).toFixed(2)),
          }),
        );
      } catch {
        // Ignore canvas draw errors before first frame decodes
      }
    };

    liveLoopTimerRef.current = window.setInterval(captureAndSend, 500);
  };

  const handleStartLiveCamera = async () => {
    stopLiveSession();
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ video: { width: 1280, height: 720 } });
      liveStreamRef.current = stream;
      setLiveCameraActive(true);
      setDrawerOpen(false);
      if (videoRef.current) {
        videoRef.current.srcObject = stream;
        await videoRef.current.play();
      }
    } catch {
      setDrawerOpen(false);
    }
  };

  const handleSignOut = () => {
    if (videoRef.current && !videoRef.current.paused) {
      try {
        videoRef.current.pause();
      } catch {
        // Ignore unimplemented media pause in headless test environments
      }
    }
    setStoredToken("");
    setUser(null);
    setSettingsOpen(false);
    setDrawerOpen(false);
    setMediaModal(null);
    setAuthError("");
    window.google?.accounts?.id?.disableAutoSelect?.();
  };

  if (viewMode === "privacy" || viewMode === "terms") {
    const isPrivacy = viewMode === "privacy";
    return (
      <div className="app-shell">
        <main className="legal-page" data-testid={isPrivacy ? "privacy-page" : "terms-page"}>
          <div className="legal-header">
            <div className="legal-brand">
              <img src="/logo-mark.svg" alt="RollMetrics mark" style={{ width: 24, height: 24 }} />
              <span>RollMetrics</span>
            </div>
            <button
              type="button"
              className="mini-btn"
              data-testid="legal-back-btn"
              onClick={() => navigateViewMode("player")}
            >
              &lt;- Back
            </button>
          </div>
          {isPrivacy ? (
            <article className="legal-body">
              <h1>Privacy Policy</h1>
              <p className="legal-updated">Effective 2026</p>
              <p>
                We collect only the basic account details needed to sign you in and the match videos
                or coaching notes you choose to upload.
              </p>
              <p>
                We use your uploads solely to generate match scoring and coaching summaries for your
                account.
              </p>
              <p>
                Your private uploads remain scoped to your account, we never sell your data, and you
                can request export or deletion of your account and videos at any time.
              </p>
            </article>
          ) : (
            <article className="legal-body">
              <h1>Terms of Service</h1>
              <p className="legal-updated">Effective 2026</p>
              <p>
                RollMetrics is an educational Jiu-Jitsu film-study and scoring tool. Automated
                rulings and coaching summaries are informational only and do not replace official
                referees, coaches, or medical judgment.
              </p>
              <p>
                Only upload videos you have permission to share. You retain ownership of all videos
                and notes you upload.
              </p>
              <p>The service is provided as-is without warranties of any kind.</p>
            </article>
          )}
          <footer className="legal-footer">
            <button
              type="button"
              className="legal-link-btn"
              onClick={() => navigateViewMode("privacy")}
            >
              Privacy
            </button>
            <span>·</span>
            <button
              type="button"
              className="legal-link-btn"
              onClick={() => navigateViewMode("terms")}
            >
              Terms
            </button>
          </footer>
        </main>
      </div>
    );
  }

  if (shareToken && !sharedMatch) {
    return (
      <div className="app-shell">
        <div className="auth-gate" data-testid="share-loading-gate">
          <img src="/logo-mark.svg" alt="RollMetrics mark" className="auth-gate-mark" />
          <div className="auth-gate-wordmark">RollMetrics</div>
          <p className="auth-gate-subtitle">
            {shareLoadError || "Loading shared match breakdown..."}
          </p>
          {shareLoadError && (
            <button
              type="button"
              className="mini-btn"
              onClick={() => {
                setShareToken("");
                navigateViewMode("player");
              }}
            >
              Open RollMetrics
            </button>
          )}
        </div>
      </div>
    );
  }

  if (!shareToken && !authConfig) {
    return (
      <div className="app-shell">
        <div className="auth-boot" aria-label="Loading RollMetrics">
          <img src="/logo-mark.svg" alt="RollMetrics mark" style={{ width: 44, height: 44 }} />
        </div>
      </div>
    );
  }

  if (!shareToken && authConfig?.auth_required && !user) {
    return (
      <LandingPage
        gisButtonRef={gisButtonRef}
        authError={authError}
        formatAuthGateError={formatAuthGateError}
        onNavigateLegal={navigateViewMode}
        onDemoLogin={() => void handleDemoLogin()}
        demoLoginBusy={demoLoginBusy}
      />
    );
  }

  const videoSrc = liveSimBlobUrl
    ? liveSimBlobUrl
    : activeMatch.video_url
      ? shareToken
        ? activeMatch.video_url
        : withAuthUrl(activeMatch.video_url)
      : withAuthUrl(`/api/matches/${activeMatch.id}/video`);

  const posterSrc =
    liveCameraActive || liveSimBlobUrl
      ? undefined
      : activeMatch.poster_url
        ? activeMatch.poster_url
        : activeMatch.video_url
          ? undefined
          : withAuthUrl(`/api/matches/${activeMatch.id}/poster`);

  const displayedEvents = liveSimActive ? liveEvents : activeMatch.events;
  const activeMomentIndex =
    displayedEvents.length === 0
      ? -1
      : Math.max(
          0,
          displayedEvents.findIndex((ev) => ev.id === (selectedEventId || highlightedEventId)),
        );

  const activeCoachSummary = activeMatch.coach_summary || {
    overview: `${activeMatch.athlete_a.name} vs. ${activeMatch.athlete_b.name} (${activeMatch.belt_label}). Review each match call to inspect the key grip, stabilization, and finishing exchanges.`,
    athlete_a: {
      name: activeMatch.athlete_a.name,
      gi: activeMatch.athlete_a.gi,
      cues: [
        "Establish first grip before pulling guard or shooting.",
        "Hold scoring positions for a full 3.0 seconds before chaining attacks.",
      ],
      cta_links: [
        {
          label: "CTA Guard & Control Fundamentals",
          url: "https://www.youtube.com/watch?v=4-7AYrBCOvQ&t=122s",
        },
      ],
    },
    athlete_b: {
      name: activeMatch.athlete_b.name,
      gi: activeMatch.athlete_b.gi,
      cues: [
        "Protect inside elbow space early to prevent opponent stabilization.",
        "Stay active in neutral exchanges to avoid stalling penalties.",
      ],
      cta_links: [
        {
          label: "CTA Posture & Defense Fundamentals",
          url: "https://www.youtube.com/watch?v=4-7AYrBCOvQ&t=4797s",
        },
      ],
    },
  };

  const handleStepMoment = (delta: -1 | 1) => {
    if (displayedEvents.length === 0) return;
    const base = activeMomentIndex >= 0 ? activeMomentIndex : 0;
    const nextIdx = (base + delta + displayedEvents.length) % displayedEvents.length;
    handleSeekToEvent(displayedEvents[nextIdx]);
  };
  const displayScore = liveSimActive
    ? {
        ptsA: liveScore.ptsA,
        advA: liveScore.advA,
        penA: liveScore.penA,
        ptsB: liveScore.ptsB,
        advB: liveScore.advB,
        penB: liveScore.penB,
        finished: liveScore.finished,
        clock: formatCountdown(Math.max(0, activeMatch.regulation_sec - currentTime)),
        stabilizingNow: Boolean(liveScore.stabKind),
        stabLabel: liveScore.stabKind
          ? `3.0s CONTROL · ${liveScore.stabHeldSec.toFixed(1)}s`
          : "3.0s CONTROL",
      }
    : {
        ...syncedState,
        stabLabel: "3.0s CONTROL",
      };

  const academyLabel = shareToken ? sharedAcademyName : academy?.academy_name || "Academy Library";
  const academyShortLabel =
    academyLabel === "Caio Terra Association" ? "CTA Curriculum" : academyLabel;
  const academyLinkLabel = academyLabel === "Caio Terra Association" ? "Caio Terra" : academyLabel;

  const progressPct = Math.min(
    100,
    Math.max(0, (currentTime / effectiveDuration) * 100),
  );

  return (
    <div className="app-shell">
      {/* 48px Quiet Header: 1) Kamon Lock + RollMetrics  2) Match Pills  3) Belt Chip + Mode Dot */}
      <header className="topbar">
        <button
          type="button"
          className="brand-lockup"
          data-testid="brand-home-btn"
          aria-label="RollMetrics Overview Home"
          onClick={() => {
            if (shareToken) {
              setShareToken("");
              navigateViewMode("player");
            } else {
              navigateViewMode(viewMode === "home" ? "player" : "home");
            }
          }}
        >
          <img src="/logo-mark.svg" alt="RollMetrics Kamon Lock" />
          <span className="brand-wordmark">RollMetrics</span>
        </button>

        {shareToken ? (
          <div className="match-selector-bar" data-testid="shared-breakdown-banner">
            <span className="shared-pill">Shared Breakdown · {activeMatch.pill}</span>
          </div>
        ) : (
        <div className="match-selector-bar">
          <div className="match-select-wrap">
            <select
              aria-label="Match Selector"
              className="match-select"
              value={activeMatch.id}
              onChange={(e) => handleSelectMatch(e.target.value)}
            >
              {matches.some((m) => m.bucket === "my_matches") && (
                <optgroup label="My Matches">
                  {matches
                    .filter((m) => m.bucket === "my_matches")
                    .map((m) => (
                      <option key={m.id} value={m.id}>
                        {m.pill}
                      </option>
                    ))}
                </optgroup>
              )}
              <optgroup label="Film Study">
                {matches
                  .filter((m) => m.bucket !== "my_matches")
                  .map((m) => (
                    <option key={m.id} value={m.id}>
                      {m.pill}
                    </option>
                  ))}
              </optgroup>
            </select>
            <span className="match-select-chevron" aria-hidden="true">
              ▾
            </span>
          </div>
          {liveSimActive || liveCameraActive ? (
            <div className="live-controls-bar">
              <span className="live-sim-badge" data-testid="live-sim-badge">
                <span className="live-dot" aria-hidden="true" />
                {liveSimActive ? "LIVE SIM" : "LIVE CAM"}
              </span>
              <button
                type="button"
                className="stop-live-btn"
                data-testid="stop-live-btn"
                onClick={stopLiveSession}
              >
                Stop Live
              </button>
            </div>
          ) : (
            <>
              <button
                type="button"
                className="analyze-trigger-btn"
                aria-label="Analyze or Live Camera"
                onClick={() => {
                  setSimMatchId(activeMatch.id);
                  setDrawerOpen(true);
                }}
              >
                <span className="analyze-plus" aria-hidden="true">
                  +
                </span>
                <span className="analyze-label">Analyze / Live</span>
              </button>
            </>
          )}
        </div>
        )}

        <div className="topbar-right">
          {shareToken ? (
            <button
              type="button"
              className="mini-btn"
              data-testid="share-open-app-btn"
              onClick={() => {
                setShareToken("");
                navigateViewMode("player");
              }}
            >
              Open App
            </button>
          ) : (
            <>
              <button
                type="button"
                className="user-trigger-btn"
                data-testid="user-profile"
                aria-label="Open Connection and Account Settings"
                onClick={() => void handleOpenSettings()}
              >
                {user?.picture ? (
                  <img src={user.picture} alt={user.name} className="user-avatar" />
                ) : (
                  <span className="user-avatar" aria-hidden="true">
                    {getInitials(user?.name || "Kory Mathewson")}
                  </span>
                )}
                <span className="user-name">{user?.name || "Kory Mathewson"}</span>
                <span className="settings-gear" aria-hidden="true">
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                    <circle cx="12" cy="12" r="3" />
                    <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09a1.65 1.65 0 0 0-1-1.51 1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09a1.65 1.65 0 0 0 1.51-1 1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33h.08a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51h.08a1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82v.08a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
                  </svg>
                </span>
              </button>
              {(authConfig?.auth_required || user?.auth_mode === "google_id_token") && (
                <button
                  type="button"
                  className="analyze-trigger-btn topbar-signout-btn"
                  data-testid="topbar-signout"
                  onClick={handleSignOut}
                >
                  Sign Out
                </button>
              )}
            </>
          )}
        </div>
      </header>

      {!shareToken && viewMode === "home" ? (
        <main className="overview-home" data-testid="overview-home">
          {personalSummary && personalSummary.match_count > 0 && (
            <section className="gameplan-card" data-testid="my-game-plan">
              <div className="overview-library-head">
                <h2>My Game Plan</h2>
                <div className="gameplan-stats">
                  <span className="win-pill">{personalSummary.record.label}</span>
                  <span className="gameplan-stat-muted">
                    {personalSummary.points_allowed} pts allowed
                  </span>
                </div>
              </div>
              <p className="coach-overview-text">{personalSummary.summary_line}</p>
              {personalSummary.recurring_positions.length > 0 && (
                <div className="scouting-sub-block">
                  <div className="coach-section-label">Recurring Positions</div>
                  <div className="gameplan-positions">
                    {personalSummary.recurring_positions.map((pos) => (
                      <span key={pos} className="gameplan-pos-pill">
                        {pos}
                      </span>
                    ))}
                  </div>
                </div>
              )}
              {personalSummary.top_cta_priorities.length > 0 && (
                <div className="scouting-sub-block">
                  <div className="coach-section-label">
                    Recommended Study Videos ({academyShortLabel})
                  </div>
                  <div className="scouting-video-grid">
                    {personalSummary.top_cta_priorities.map((item, idx) => {
                      const ts = formatVideoTimestamp(item.url);
                      return (
                        <button
                          key={item.url + item.title}
                          type="button"
                          className="scouting-video-chip"
                          data-testid={`gameplan-video-btn-${idx}`}
                          onClick={() =>
                            setReferenceModal({
                              title: item.title,
                              subtitle: item.cue,
                              url: item.url,
                            })
                          }
                        >
                          <span aria-hidden="true">▶</span>
                          <span>{item.title}</span>
                          {ts && <span className="scouting-video-ts">{ts}</span>}
                        </button>
                      );
                    })}
                  </div>
                </div>
              )}
            </section>
          )}

          <section className="overview-library" data-testid="home-my-matches">
            <div className="overview-library-head">
              <h2>My Matches</h2>
              <div style={{ display: "flex", gap: "0.45rem", flexWrap: "wrap" }}>
                <button
                  type="button"
                  className="mini-btn"
                  data-testid="open-upload-modal-btn"
                  onClick={() => {
                    setUploadBucket("my_matches");
                    setUploadError("");
                    setUploadModalOpen(true);
                  }}
                >
                  Upload Match
                </button>
                <button
                  type="button"
                  className="analyze-trigger-btn"
                  onClick={() => {
                    setSimMatchId(activeMatch.id);
                    setDrawerOpen(true);
                  }}
                >
                  + Analyze / Live
                </button>
              </div>
            </div>
            <div className="match-library-grid">
              {matches
                .filter((m) => m.bucket === "my_matches")
                .map((m) => (
                  <button
                    key={m.id}
                    type="button"
                    className="match-library-card"
                    data-testid={`home-match-${m.id}`}
                    onClick={() => handleSelectMatch(m.id)}
                  >
                    <div className="library-card-top">
                      <span className="library-card-pill">{m.pill}</span>
                      <span className="win-pill">{compactWinBadge(m.win_label)}</span>
                    </div>
                    <div className="library-card-meta">
                      <BeltSwatch belt={m.belt} />
                      <span>{m.belt_label}</span>
                    </div>
                    <div className="library-card-sub">{m.title}</div>
                  </button>
                ))}
            </div>
          </section>

          <section className="overview-library" data-testid="home-film-study">
            <div className="overview-library-head">
              <h2>Film Study</h2>
              <div className="film-study-filters" role="group" aria-label="Filter Film Study by athlete">
                <button
                  type="button"
                  data-testid="film-study-filter-all"
                  className={`filter-chip ${filmStudyAthlete === "" ? "active" : ""}`}
                  onClick={() => setFilmStudyAthlete("")}
                >
                  All ({filmStudyMatches.length})
                </button>
                {filmStudyAthletes.map((a) => (
                  <button
                    key={a.name}
                    type="button"
                    data-testid={`film-study-filter-${slugifyName(a.name)}`}
                    className={`filter-chip ${filmStudyAthlete === a.name ? "active" : ""}`}
                    onClick={() => setFilmStudyAthlete(a.name)}
                  >
                    {a.name} ({a.count})
                  </button>
                ))}
              </div>
            </div>

            {filmStudyAthlete && filmStudySummary && (
              <div className="scouting-card" data-testid="film-study-scouting-card">
                <div className="overview-library-head">
                  <div>
                    <div className="coach-section-label">Scouting &amp; Game Summary</div>
                    <strong className="scouting-athlete-title">
                      {filmStudySummary.focus_athlete}
                    </strong>
                  </div>
                  <div className="gameplan-stats">
                    <span className="win-pill">{filmStudySummary.record_label}</span>
                    <span className="gameplan-stat-muted">
                      {filmStudySummary.combined_points_for}-{filmStudySummary.combined_points_against} pts
                    </span>
                  </div>
                </div>
                <p className="coach-overview-text">{filmStudySummary.summary_text}</p>
                {filmStudySummary.a_game_patterns.filter((p) => !/medical stoppage/i.test(p)).length > 0 && (
                  <div className="scouting-sub-block">
                    <div className="coach-section-label">Signature Techniques &amp; Scoring Positions</div>
                    <div className="gameplan-positions">
                      {filmStudySummary.a_game_patterns
                        .filter((p) => !/medical stoppage/i.test(p))
                        .map((pat) => (
                          <span key={pat} className="gameplan-pos-pill">
                            {pat}
                          </span>
                        ))}
                    </div>
                  </div>
                )}
                {filmStudySummary.coaching_takeaways.length > 0 && (
                  <div className="scouting-sub-block">
                    <div className="coach-section-label">Coaching Notes</div>
                    <ul className="coach-cue-list">
                      {filmStudySummary.coaching_takeaways.map((cue) => (
                        <li key={cue}>{cue}</li>
                      ))}
                    </ul>
                  </div>
                )}
                {filmStudySummary.cta_links.length > 0 && (
                  <div className="scouting-sub-block">
                    <div className="coach-section-label">
                      Recommended Study Videos ({academyShortLabel})
                    </div>
                    <div className="scouting-video-grid">
                      {filmStudySummary.cta_links.map((item, idx) => {
                        const ts = formatVideoTimestamp(item.url);
                        return (
                          <button
                            key={item.url + item.title}
                            type="button"
                            className="scouting-video-chip"
                            data-testid={`scouting-video-btn-${idx}`}
                            onClick={() =>
                              setReferenceModal({
                                title: item.title,
                                subtitle: filmStudySummary.focus_athlete,
                                url: item.url,
                              })
                            }
                          >
                            <span aria-hidden="true">▶</span>
                            <span>{item.title}</span>
                            {ts && <span className="scouting-video-ts">{ts}</span>}
                          </button>
                        );
                      })}
                    </div>
                  </div>
                )}
              </div>
            )}

            <div className="match-library-grid">
              {filteredFilmStudyMatches.map((m) => (
                <button
                  key={m.id}
                  type="button"
                  className="match-library-card"
                  data-testid={`home-match-${m.id}`}
                  onClick={() => handleSelectMatch(m.id)}
                >
                  <div className="library-card-top">
                    <span className="library-card-pill">{m.pill}</span>
                    <span className="win-pill">{compactWinBadge(m.win_label)}</span>
                  </div>
                  <div className="library-card-meta">
                    <BeltSwatch belt={m.belt} />
                    <span>{m.belt_label}</span>
                  </div>
                  <div className="library-card-sub">{m.title}</div>
                </button>
              ))}
            </div>
          </section>
          <footer
            data-testid="build-stamp-home"
            className="overview-footer-row"
          >
            <span>
              RollMetrics v{authConfig?.app_version || "0.1.0"} · {authConfig?.build_sha || "local"}
            </span>
            <span className="legal-links-row" data-testid="home-legal-links">
              <button
                type="button"
                className="legal-link-btn"
                onClick={() => navigateViewMode("privacy")}
              >
                Privacy
              </button>
              <span>·</span>
              <button
                type="button"
                className="legal-link-btn"
                onClick={() => navigateViewMode("terms")}
              >
                Terms
              </button>
            </span>
          </footer>
        </main>
      ) : !shareToken && viewMode === "admin" ? (
        <main className="admin-dashboard" data-testid="admin-dashboard">
          <div className="admin-header">
            <div>
              <h1 className="admin-title">App Admin</h1>
              <p className="admin-subtitle">
                Athletes, teams, curricula, and active scoring rulesets.
              </p>
            </div>
            <div style={{ display: "flex", gap: "0.5rem", flexWrap: "wrap" }}>
              <button
                type="button"
                className="mini-btn"
                data-testid="switch-ml-admin-btn"
                onClick={() => navigateViewMode("ml-admin")}
              >
                ML Admin -&gt;
              </button>
              <button
                type="button"
                className="mini-btn"
                data-testid="admin-back-btn"
                onClick={() => navigateViewMode("player")}
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
                      {(adminOverview?.athletes || adminOverview?.users_and_usage || []).map((u) => (
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
                      {(adminOverview?.teams || []).map((tm) => (
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
                      {(adminOverview?.curricula || []).map((c) => (
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
                      {(adminOverview?.rulesets || []).map((rs) => (
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
            RollMetrics v{adminOverview?.app_version || authConfig?.app_version || "0.1.0"} ·{" "}
            {adminOverview?.build_sha || authConfig?.build_sha || "local"} ·{" "}
            {adminOverview?.environment || authConfig?.environment || "local"}
          </footer>
        </main>
      ) : !shareToken && viewMode === "ml-admin" ? (
        <main className="admin-dashboard" data-testid="ml-admin-dashboard">
          <div className="admin-header">
            <div>
              <h1 className="admin-title">ML Admin &amp; Evaluation Lab</h1>
              <p className="admin-subtitle">
                Match Moments engine versions, blind-video benchmarks, dataset integrity, and experiments.
              </p>
            </div>
            <div style={{ display: "flex", gap: "0.5rem", flexWrap: "wrap" }}>
              <button
                type="button"
                className="mini-btn"
                data-testid="switch-app-admin-btn"
                onClick={() => navigateViewMode("admin")}
              >
                App Admin -&gt;
              </button>
              <button
                type="button"
                className="mini-btn"
                data-testid="ml-admin-back-btn"
                onClick={() => navigateViewMode("player")}
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
                      onClick={() => setAdminSplit("heldout")}
                    >
                      Heldout
                    </button>
                    <button
                      type="button"
                      data-testid="admin-split-calibration"
                      className={`rail-seg-btn ${adminSplit === "calibration" ? "active" : ""}`}
                      onClick={() => setAdminSplit("calibration")}
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
                      {(adminOverview?.scoreboard_eval.splits?.[adminSplit] || []).map((row) => (
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
                      {(adminOverview?.scoreboard_eval.splits?.[adminSplit] || []).length === 0 && (
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
                  <h2>Deterministic Rules &amp; Knowledge Base Evals</h2>
                </div>
                <div className="admin-kpi-grid" data-testid="admin-kb-strip">
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
                    <span className="admin-kpi-label">IBJJF Rule Cases</span>
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
                    <span className="admin-kpi-label">CTA Recall@3</span>
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
            RollMetrics v{adminOverview?.app_version || authConfig?.app_version || "0.1.0"} ·{" "}
            {adminOverview?.build_sha || authConfig?.build_sha || "local"} ·{" "}
            {adminOverview?.environment || authConfig?.environment || "local"}
          </footer>
        </main>
      ) : (
        /* Single-Screen Stage: 100dvh Zero-Scroll Desktop Fit */
        <main className="arena-stage">
        <section className="video-column" aria-label="Match Video and Scoreboard">
          <div className="video-frame" ref={videoFrameRef}>
            <video
              ref={videoRef}
              key={`${liveCameraActive ? "live-cam" : liveSimBlobUrl || activeMatch.id}-${videoRetryKey}`}
              src={liveCameraActive ? undefined : videoSrc}
              poster={posterSrc}
              playsInline
              {...({ "webkit-playsinline": "true" } as Record<string, string>)}
              preload="auto"
              style={{ cursor: "pointer" }}
              onClick={togglePlayPause}
              onWaiting={() => setVideoBuffering(true)}
              onCanPlay={() => {
                setVideoBuffering(false);
                setVideoError(false);
              }}
              onPlaying={() => {
                setIsPlaying(true);
                setVideoBuffering(false);
                setVideoError(false);
              }}
              onPlay={() => setIsPlaying(true)}
              onPause={() => {
                setIsPlaying(false);
                setVideoBuffering(false);
              }}
              onError={() => {
                setIsPlaying(false);
                setVideoBuffering(false);
                setVideoError(true);
              }}
              onLoadedMetadata={(e) => {
                if (Number.isFinite(e.currentTarget.duration) && e.currentTarget.duration > 0) {
                  setVideoDuration(e.currentTarget.duration);
                }
              }}
              onTimeUpdate={(e) => setCurrentTime(e.currentTarget.currentTime)}
            />
            {videoBuffering && !videoError && (
              <div className="video-buffering-spinner" aria-label="Buffering video" />
            )}
            {videoError && !liveCameraActive && (
              <div className="video-error-overlay" role="alert">
                <span>Video stream interrupted.</span>
                <button
                  type="button"
                  className="mini-btn"
                  onClick={() => {
                    setVideoError(false);
                    setVideoBuffering(true);
                    setVideoRetryKey((k) => k + 1);
                  }}
                >
                  Retry
                </button>
              </div>
            )}
            {!isPlaying && !liveCameraActive && !videoError && (
              <button
                type="button"
                className="video-play-overlay"
                aria-label="Start playback"
                onClick={togglePlayPause}
              >
                <svg width="26" height="26" viewBox="0 0 24 24" fill="currentColor">
                  <polygon points="7,4 20,12 7,20" />
                </svg>
              </button>
            )}
          </div>

          <div className="telemetry-dock">
            {/* 2-Row Pro Video Transport: Row 1 Full-Width Scrubber + Color Ticks, Row 2 Controls & Moment Stepper (#109) */}
            <div className="unified-transport" aria-label="Unified Video Transport">
              <div
                className="scrubber-hitbox"
                data-testid="scrubber-track"
                aria-label="Event Timeline Markers"
                onClick={(e) => {
                  if ((e.target as HTMLElement).closest(".scrubber-tick")) return;
                  seekScrubberAtClientX(e.clientX);
                }}
                onPointerDown={(e) => {
                  if ((e.target as HTMLElement).closest(".scrubber-tick")) return;
                  scrubbingRef.current = true;
                  e.currentTarget.setPointerCapture?.(e.pointerId);
                  seekScrubberAtClientX(e.clientX);
                }}
                onPointerMove={(e) => {
                  if (!scrubbingRef.current) return;
                  seekScrubberAtClientX(e.clientX);
                }}
                onPointerUp={(e) => {
                  if (!scrubbingRef.current) return;
                  scrubbingRef.current = false;
                  e.currentTarget.releasePointerCapture?.(e.pointerId);
                }}
              >
                <div className="scrubber-rail" ref={scrubberRailRef}>
                  <div className="scrubber-progress" style={{ width: `${progressPct}%` }} />
                  <span className="scrubber-playhead" style={{ left: `${progressPct}%` }} />
                  {displayedEvents.map((ev, idx) => {
                    const num = idx + 1;
                    const leftPct = Math.min(
                      99,
                      Math.max(1, (ev.video_sec / effectiveDuration) * 100),
                    );
                    const evActor = ev.call.includes("EACH")
                      ? "Both"
                      : ev.actor === "B"
                        ? labelB
                        : labelA;
                    const isHovered = hoveredEventId === ev.id;
                    const tone = callToneClass(ev.call) || "ctrl";
                    const edgeClass = leftPct < 22 ? "edge-left" : leftPct > 72 ? "edge-right" : "";
                    const tooltipText = `#${num} · ${evActor} · ${ev.call} · ${ev.detail}`;
                    return (
                      <div
                        key={ev.id}
                        className="scrubber-pin-wrap"
                        style={{ left: `${leftPct}%` }}
                        onMouseEnter={() => setHoveredEventId(ev.id)}
                        onMouseLeave={() => setHoveredEventId(null)}
                      >
                        {isHovered && (
                          <div className={`pin-tooltip ${edgeClass}`} role="tooltip">
                            {tooltipText}
                          </div>
                        )}
                        <button
                          type="button"
                          data-testid={`pin-${num}`}
                          className={`scrubber-tick tick-${tone} ${
                            highlightedEventId === ev.id ? "active" : ""
                          } ${isHovered ? "cross-hover" : ""}`}
                          aria-label={`Jump to #${num} ${evActor} ${ev.call}`}
                          onClick={(e) => {
                            e.stopPropagation();
                            handleSeekToEvent(ev);
                          }}
                        />
                      </div>
                    );
                  })}
                </div>
              </div>

              <div className="transport-controls-row">
                <div className="transport-left-group">
                  <button
                    type="button"
                    className="transport-btn"
                    aria-label={isPlaying ? "Pause video" : "Play video"}
                    onClick={togglePlayPause}
                  >
                    {isPlaying ? (
                      <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor">
                        <rect x="6" y="4" width="4" height="16" rx="1" />
                        <rect x="14" y="4" width="4" height="16" rx="1" />
                      </svg>
                    ) : (
                      <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor">
                        <polygon points="6,4 20,12 6,20" />
                      </svg>
                    )}
                  </button>

                  {displayedEvents.length > 0 && (
                    <div className="moment-stepper" data-testid="moment-stepper">
                      <button
                        type="button"
                        className="stepper-btn"
                        aria-label="Previous match moment"
                        onClick={() => handleStepMoment(-1)}
                      >
                        ‹
                      </button>
                      <span className="stepper-count">
                        {activeMomentIndex + 1} / {displayedEvents.length}
                      </span>
                      <button
                        type="button"
                        className="stepper-btn"
                        aria-label="Next match moment"
                        onClick={() => handleStepMoment(1)}
                      >
                        ›
                      </button>
                    </div>
                  )}
                </div>

                <div className="transport-right-group">
                  <span className="transport-time" data-testid="transport-time">
                    {formatCountdown(currentTime)} / {formatCountdown(effectiveDuration)}
                  </span>

                  <button
                    type="button"
                    className="transport-btn"
                    aria-label={isMuted ? "Unmute video" : "Mute video"}
                    onClick={toggleMute}
                  >
                    {isMuted ? (
                      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                        <polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5" />
                        <line x1="23" y1="9" x2="17" y2="15" />
                        <line x1="17" y1="9" x2="23" y2="15" />
                      </svg>
                    ) : (
                      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                        <polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5" />
                        <path d="M15.54 8.46a5 5 0 0 1 0 7.07" />
                      </svg>
                    )}
                  </button>

                  <button
                    type="button"
                    className="transport-btn"
                    aria-label="Toggle fullscreen"
                    onClick={toggleFullscreen}
                  >
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                      <polyline points="15 3 21 3 21 9" />
                      <polyline points="9 21 3 21 3 15" />
                      <line x1="21" y1="3" x2="14" y2="10" />
                      <line x1="3" y1="21" x2="10" y2="14" />
                    </svg>
                  </button>
                </div>
              </div>
            </div>

            {liveSimActive && (
              <div className="live-telemetry-strip" data-testid="live-telemetry-strip">
                <span>
                  Causal ·{" "}
                  {liveTelemetry.latencyMs !== null
                    ? `Last inference: ${(liveTelemetry.latencyMs / 1000).toFixed(1)}s`
                    : "Streaming live frames..."}
                  {liveTelemetry.modelId ? ` · ${liveTelemetry.modelId}` : ""}
                </span>
                <span>{liveTelemetry.position}</span>
              </div>
            )}

            {/* Swiss IBJJF Broadcast Scoreboard Strip */}
            <div className="scoreboard-strip" aria-label="IBJJF Scoreboard">
              <div className="scoreboard-head">
                <div className="clock-group">
                  <span className="swiss-clock" data-testid="match-clock">
                    {displayScore.clock}
                  </span>
                  <div className="belt-chip" data-testid="scoreboard-division">
                    <BeltSwatch belt={activeMatch.belt} />
                    <span className="division-full">{activeMatch.belt_label}</span>
                    <span className="division-short">{compactDivisionLabel(activeMatch.belt_label)}</span>
                  </div>
                  {displayScore.stabilizingNow && (
                    <span className="control-pill">{displayScore.stabLabel}</span>
                  )}
                </div>
                <span className="col-label">PTS</span>
                <span className="col-label">ADV</span>
                <span className="col-label">PEN</span>
              </div>

              <div className="competitor-row" data-testid="row-athlete-a">
                <div className="competitor-id">
                  <span
                    className={`gi-dot ${activeMatch.athlete_a.gi}`}
                    title={`${activeMatch.athlete_a.gi} gi`}
                  />
                  <BeltSwatch belt={activeMatch.athlete_a.belt} />
                  <span className="competitor-name">{activeMatch.athlete_a.name}</span>
                  {displayScore.finished && activeMatch.winner === "A" && (
                    <span className="win-pill">{compactWinBadge(activeMatch.win_label)}</span>
                  )}
                </div>
                <span className="num-cell pts" data-testid="pts-a">
                  {displayScore.ptsA}
                </span>
                <span className="num-cell adv" data-testid="adv-a">
                  {displayScore.advA}
                </span>
                <span className="num-cell pen" data-testid="pen-a">
                  {displayScore.penA}
                </span>
              </div>

              <div className="competitor-row" data-testid="row-athlete-b">
                <div className="competitor-id">
                  <span
                    className={`gi-dot ${activeMatch.athlete_b.gi}`}
                    title={`${activeMatch.athlete_b.gi} gi`}
                  />
                  <BeltSwatch belt={activeMatch.athlete_b.belt} />
                  <span className="competitor-name">{activeMatch.athlete_b.name}</span>
                  {displayScore.finished && activeMatch.winner === "B" && (
                    <span className="win-pill">{compactWinBadge(activeMatch.win_label)}</span>
                  )}
                </div>
                <span className="num-cell pts" data-testid="pts-b">
                  {displayScore.ptsB}
                </span>
                <span className="num-cell adv" data-testid="adv-b">
                  {displayScore.advB}
                </span>
                <span className="num-cell pen" data-testid="pen-b">
                  {displayScore.penB}
                </span>
              </div>
            </div>
          </div>
        </section>

        {/* Right Column: Unified Match Moments & Coaching Rail */}
        <aside className="event-rail" aria-label="Match Moments">
          <div className="rail-header" style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: "0.5rem" }}>
            <strong style={{ fontFamily: "var(--font-display)", fontSize: "0.88rem" }}>
              Moments ({displayedEvents.length})
            </strong>
            {!shareToken && (
              <button
                type="button"
                className="mini-btn"
                data-testid="share-match-btn"
                aria-label="Share Breakdown Link"
                onClick={() => void handleShareMatch()}
              >
                {shareCopiedMsg || "Share"}
              </button>
            )}
          </div>
          <div className="rail-list">
            <button
              type="button"
              className="moments-coach-toggle"
              data-testid="moments-coach-toggle"
              aria-expanded={coachExpanded}
              onClick={() => setCoachExpanded((prev) => !prev)}
            >
              <span>Match Coaching Summary</span>
              <span>{coachExpanded ? "▴" : "▾"}</span>
            </button>
            {coachExpanded && (
              <div className="coach-summary-panel" data-testid="coach-summary-panel" style={{ marginBottom: "0.65rem" }}>
                <div className="coach-overview-block">
                  <div className="coach-section-label">Overview</div>
                  <p className="coach-overview-text">{activeCoachSummary.overview}</p>
                </div>
                {[activeCoachSummary.athlete_a, activeCoachSummary.athlete_b].map((athleteAdvice) => (
                  <div key={athleteAdvice.name} className="coach-athlete-card">
                    <div className="coach-athlete-head">
                      <span className={`gi-dot ${athleteAdvice.gi}`} />
                      <span className="coach-athlete-name">{athleteAdvice.name}</span>
                    </div>
                    <ul className="coach-cue-list">
                      {athleteAdvice.cues.map((cue) => (
                        <li key={cue}>{cue}</li>
                      ))}
                    </ul>
                    <div className="coach-links">
                      {athleteAdvice.cta_links.map((link, linkIdx) => {
                        const linkKey = `${athleteAdvice.name}:${link.url}:${link.label}`;
                        const isExpanded = coachOpenLinkKey === linkKey;
                        const ts = formatVideoTimestamp(link.url);
                        return (
                          <div key={linkKey} className="coach-link-group">
                            <button
                              type="button"
                              className="cta-jump cta-inline-btn"
                              data-testid={`coach-video-btn-${slugifyName(athleteAdvice.name)}-${linkIdx}`}
                              aria-expanded={isExpanded}
                              onClick={() =>
                                setCoachOpenLinkKey((prev) => (prev === linkKey ? null : linkKey))
                              }
                            >
                              {academyShortLabel} · {link.label}
                              {ts ? ` (${ts})` : ""} {isExpanded ? "▴" : "▾"}
                            </button>
                            {isExpanded && (
                              <EmbeddedReferenceVideo
                                url={link.url}
                                title={link.label}
                                resolveVideoUrl={(u) => (shareToken ? u : withAuthUrl(u))}
                                testId={`coach-video-embed-${slugifyName(athleteAdvice.name)}-${linkIdx}`}
                              />
                            )}
                          </div>
                        );
                      })}
                    </div>
                  </div>
                ))}
              </div>
            )}

            {displayedEvents.map((ev, idx) => {
              const num = idx + 1;
              const isSelected = ev.id === (selectedEventId || highlightedEventId);
              const isHovered = hoveredEventId === ev.id;
              const isBoth = ev.call.includes("EACH");
              const actorShort = isBoth ? "Both" : ev.actor === "B" ? labelB : labelA;
              const actorGi = isBoth
                ? "both"
                : ev.actor === "B"
                  ? activeMatch.athlete_b.gi
                  : activeMatch.athlete_a.gi;
              const ctaTs = formatVideoTimestamp(ev.cta_info?.video_url || ev.cta_url, ev.cta_info?.start_sec);
              const inlineGif = inlineGifByEvent[ev.id];
              const isGifOpen = inlineGifOpenId === ev.id && Boolean(inlineGif);

              return (
                <div
                  key={ev.id}
                  data-testid={`call-row-${num}`}
                  className={`event-item ${isSelected ? "active" : ""} ${isHovered ? "cross-hover" : ""}`}
                  onMouseEnter={() => setHoveredEventId(ev.id)}
                  onMouseLeave={() => setHoveredEventId(null)}
                >
                  <button
                    type="button"
                    className="event-line"
                    onClick={() => handleSeekToEvent(ev)}
                  >
                    <span className="ev-num">{num}</span>
                    <span className="ev-actor" data-testid={`actor-${ev.id}`}>
                      <span className={`gi-dot ${actorGi}`} />
                      <span>{actorShort}</span>
                    </span>
                    <span className={`ev-call ${callToneClass(ev.call)}`}>{ev.call}</span>
                    <span className="ev-detail">{ev.detail}</span>
                  </button>

                  {isSelected && (
                    <div className="event-drawer-footer">
                      <div className="event-citation-row">
                        <span className="event-citation-text">
                          {formatCallPrefix(ev, actorShort, isBoth)} ·{" "}
                          <button
                            type="button"
                            className="rule-cite-btn"
                            data-testid="rule-cite-btn"
                            aria-expanded={rulePeekEventId === ev.id}
                            onClick={() =>
                              setRulePeekEventId((prev) => (prev === ev.id ? null : ev.id))
                            }
                          >
                            {ev.rule} {rulePeekEventId === ev.id ? "▴" : "▾"}
                          </button>
                        </span>
                        <div style={{ display: "inline-flex", alignItems: "center", gap: "0.45rem", flexWrap: "wrap" }}>
                          <button
                            type="button"
                            className="rule-cite-btn cta-jump cta-inline-btn"
                            data-testid="cta-peek-btn"
                            aria-expanded={ctaPeekEventId === ev.id}
                            onClick={() =>
                              setCtaPeekEventId((prev) => (prev === ev.id ? null : ev.id))
                            }
                          >
                            {academyLinkLabel}
                            {ctaTs ? ` · ${ctaTs}` : ""} {ctaPeekEventId === ev.id ? "▴" : "▾"}
                          </button>
                          <button
                            type="button"
                            className="rule-cite-btn"
                            data-testid="highlight-btn"
                            aria-expanded={isGifOpen}
                            disabled={mediaBusy}
                            onClick={() => void handleRenderHighlight(ev, num)}
                          >
                            {mediaBusy ? "GIF..." : `GIF ${isGifOpen ? "▴" : "▾"}`}
                          </button>
                        </div>
                      </div>

                      {isGifOpen && inlineGif && (
                        <div className="inline-gif-box" data-testid="highlight-lightbox">
                          <div className="inline-gif-head">
                            <span>{inlineGif.label}</span>
                            <a
                              href={inlineGif.url}
                              download
                              className="cta-jump"
                              data-testid="highlight-download-link"
                              style={{ textDecoration: "none" }}
                            >
                              Download
                            </a>
                          </div>
                          <video
                            src={inlineGif.url}
                            data-testid="highlight-video"
                            controls
                            autoPlay
                            loop
                            muted
                            playsInline
                            {...({ "webkit-playsinline": "true" } as Record<string, string>)}
                          />
                        </div>
                      )}

                      {ctaPeekEventId === ev.id && (
                        <div className="rule-peek-box" data-testid="cta-peek-box">
                          <div className="rule-peek-title">
                            {ev.cta_info?.title || ev.detail}
                          </div>
                          <p className="rule-peek-excerpt">
                            {ev.cta_info?.excerpt ||
                              `${academy?.academy_name || "Academy Library"} coaching reference for ${ev.detail}.`}
                          </p>
                          <EmbeddedReferenceVideo
                            url={ev.cta_info?.video_url || ev.cta_url}
                            title={ev.cta_info?.title || ev.detail}
                            startSec={ev.cta_info?.start_sec}
                            resolveVideoUrl={(u) => (shareToken ? u : withAuthUrl(u))}
                            testId="cta-peek-video"
                          />
                        </div>
                      )}

                      {rulePeekEventId === ev.id && (
                        <div className="rule-peek-box" data-testid="rule-peek-box">
                          <div className="rule-peek-title">
                            {ev.rule_info?.title || ev.rule}
                          </div>
                          <p className="rule-peek-excerpt">
                            {ev.rule_info?.excerpt ||
                              `Official IBJJF Rulebook v6.1 reference for ${ev.rule} (${ev.detail}).`}
                          </p>
                          <div className="rule-peek-links">
                            <a
                              href={ev.rule_info?.rulebook_url || "https://ibjjf.com/books-videos"}
                              target="_blank"
                              rel="noreferrer"
                              className="cta-jump"
                              data-testid="rulebook-link"
                            >
                              IBJJF Rulebook -&gt;
                            </a>
                            {ev.rule_info?.ibjjf_video_url && (
                              <button
                                type="button"
                                className="rule-cite-btn cta-jump cta-inline-btn"
                                data-testid="ibjjf-video-link"
                                aria-expanded={ruleVideoOpenEventId === ev.id}
                                onClick={() =>
                                  setRuleVideoOpenEventId((prev) =>
                                    prev === ev.id ? null : ev.id,
                                  )
                                }
                              >
                                Official IBJJF Video {ruleVideoOpenEventId === ev.id ? "▴" : "▾"}
                              </button>
                            )}
                          </div>
                          {ev.rule_info?.ibjjf_video_url && ruleVideoOpenEventId === ev.id && (
                            <EmbeddedReferenceVideo
                              url={ev.rule_info.ibjjf_video_url}
                              title={ev.rule_info.ibjjf_video_title || ev.rule_info.title || ev.rule}
                              testId="ibjjf-rule-video"
                            />
                          )}
                        </div>
                      )}

                      {(ev.call.includes("PEN") ||
                        ev.call.includes("DQ") ||
                        ev.call.includes("ILLEGAL")) && (
                        <div className="penalty-ladder" data-testid="penalty-ladder">
                          <div className="ladder-steps">
                            {PENALTY_STEPS.map((ps, sIdx) => {
                              const targetStep =
                                ev.call.includes("DQ") || ev.call.includes("ILLEGAL")
                                  ? 4
                                  : ev.actor === "B"
                                    ? ev.pen_b
                                    : ev.pen_a;
                              const activeStep = isBoth
                                ? ps.step === ev.pen_a || ps.step === ev.pen_b
                                : ps.step === targetStep;
                              return (
                                <span key={ps.step}>
                                  <span className={`ladder-step ${activeStep ? "current" : ""}`}>
                                    {ps.label}
                                  </span>
                                  {sIdx < PENALTY_STEPS.length - 1 ? " · " : ""}
                                </span>
                              );
                            })}
                          </div>
                          <div className="ladder-who">
                            {isBoth
                              ? `${labelA} ● step ${ev.pen_a} · ${labelB} ● step ${ev.pen_b}`
                              : `${actorShort} ● step ${
                                  ev.call.includes("DQ") || ev.call.includes("ILLEGAL")
                                    ? 4
                                    : ev.actor === "B"
                                      ? ev.pen_b
                                      : ev.pen_a
                                }`}
                          </div>
                        </div>
                      )}

                      {ev.call.includes("ADV") &&
                        !ev.call.includes("PEN") &&
                        (typeof ev.hold_sec === "number" || ev.detail.includes("<3")) && (
                          <div className="hold-mini-box" data-testid="hold-downgrade-bar">
                            <span>
                              Hold {(ev.hold_sec ?? 1.8).toFixed(1)}s / 3.0s needed -&gt; Advantage
                            </span>
                            <div className="hold-mini-track">
                              <div
                                className="hold-mini-fill"
                                style={{
                                  width: `${Math.min(99, Math.round(((ev.hold_sec ?? 1.8) / 3.0) * 100))}%`,
                                }}
                              />
                            </div>
                          </div>
                        )}

                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </aside>
        </main>
      )}

      {/* Quiet On-Demand Analyze / Live Camera Drawer */}
      {drawerOpen && (
        <div className="overlay-backdrop" onClick={() => setDrawerOpen(false)}>
          <div className="drawer-sheet" onClick={(e) => e.stopPropagation()}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <strong style={{ fontFamily: "var(--font-display)", fontSize: "1rem" }}>
                Analyze Video or Live Mat
              </strong>
              <button type="button" className="mini-btn" onClick={() => setDrawerOpen(false)}>
                Close
              </button>
            </div>

            <div className="rail-segmented" role="group" aria-label="Save match bucket">
              <button
                type="button"
                className={`rail-seg-btn ${uploadBucket === "my_matches" ? "active" : ""}`}
                onClick={() => setUploadBucket("my_matches")}
              >
                My Match
              </button>
              <button
                type="button"
                className={`rail-seg-btn ${uploadBucket === "film_study" ? "active" : ""}`}
                onClick={() => setUploadBucket("film_study")}
              >
                Film Study
              </button>
            </div>

            <label className="mini-btn" style={{ textAlign: "center", padding: "0.6rem" }}>
              {ingestBusy ? "Analyzing..." : "Upload Match MP4 / MOV"}
              <input
                type="file"
                accept="video/mp4,video/quicktime,video/webm"
                style={{ display: "none" }}
                onChange={(e) => void handleUploadVideo(e.target.files?.[0] || null)}
              />
            </label>

            <div style={{ display: "flex", gap: "0.45rem" }}>
              <input
                className="input-clean"
                placeholder="Paste YouTube or video URL"
                value={urlInput}
                onChange={(e) => setUrlInput(e.target.value)}
              />
              <button
                type="button"
                className="mini-btn"
                disabled={ingestBusy}
                onClick={() => void handleAnalyzeUrl()}
              >
                Run
              </button>
            </div>

            <div className="config-section">
              <strong style={{ fontSize: "0.8rem", color: "var(--text-secondary)" }}>Live Mat</strong>
              <button
                type="button"
                className="mini-btn"
                style={{ padding: "0.6rem" }}
                onClick={() => void handleStartLiveCamera()}
              >
                Use Physical Camera
              </button>
              <select
                aria-label="Live Sim Match Selector"
                className="input-clean"
                value={simMatchId}
                onChange={(e) => setSimMatchId(e.target.value)}
              >
                {matches.map((m) => (
                  <option key={m.id} value={m.id}>
                    {m.pill}
                  </option>
                ))}
              </select>
              <button
                type="button"
                className="mini-btn"
                style={{ padding: "0.6rem" }}
                onClick={() => startLiveSim(simMatchId)}
              >
                Simulate Live from Match Video
              </button>
              <label className="mini-btn" style={{ textAlign: "center", padding: "0.52rem" }}>
                Simulate Live from Local MP4
                <input
                  type="file"
                  accept="video/mp4,video/quicktime,video/webm"
                  style={{ display: "none" }}
                  onChange={(e) => {
                    const f = e.target.files?.[0];
                    if (f) startLiveSim(simMatchId, f);
                  }}
                />
              </label>
            </div>
          </div>
        </div>
      )}

      {/* Connection & Model Config Modal (opened from top-right User Profile / Settings gear) */}
      {settingsOpen && (
        <div className="overlay-backdrop" onClick={() => setSettingsOpen(false)}>
          <div
            className="config-modal"
            role="dialog"
            aria-label="Connection and Model Config"
            onClick={(e) => e.stopPropagation()}
          >
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <strong style={{ fontFamily: "var(--font-display)", fontSize: "1.05rem" }}>
                Connection &amp; Model Config
              </strong>
              <button type="button" className="mini-btn" onClick={() => setSettingsOpen(false)}>
                Close
              </button>
            </div>

            <div className="engine-cards">
              <button
                type="button"
                className={`engine-card ${profile === "local" ? "selected" : ""}`}
                onClick={() => void handleSaveConfig("local")}
              >
                <div style={{ fontWeight: 700, fontSize: "0.86rem" }}>Local (Ollama Gemma 4)</div>
                <div style={{ fontSize: "0.75rem", color: "var(--text-secondary)", marginTop: 4 }}>
                  Runs on this machine ($0.00, no cloud API key needed)
                </div>
              </button>

              <button
                type="button"
                className={`engine-card ${profile === "cloud" ? "selected" : ""}`}
                onClick={() => void handleSaveConfig("cloud")}
              >
                <div style={{ fontWeight: 700, fontSize: "0.86rem" }}>Cloud (Gemini 4 Tiered)</div>
                <div style={{ fontSize: "0.75rem", color: "var(--text-secondary)", marginTop: 4 }}>
                  gemma-4-26b-a4b-it live HUD + gemini-4-hydrogen video + gemini-4-neon disputes
                </div>
              </button>
            </div>

            {profile === "local" ? (
              /* Local Connection Panel */
              <div className="config-section">
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                  <strong style={{ fontSize: "0.82rem" }}>Local Connection (Ollama)</strong>
                  <button
                    type="button"
                    className="mini-btn"
                    disabled={testingTarget !== null}
                    onClick={() => void handleTestConnection("local")}
                  >
                    {testingTarget === "local" ? "Testing..." : "Test Local Connection"}
                  </button>
                </div>
                <div className="ollama-grid">
                  <input
                    className="input-clean"
                    aria-label="Ollama Base URL"
                    value={ollamaUrlInput}
                    onChange={(e) => setOllamaUrlInput(e.target.value)}
                  />
                  <select
                    className="input-clean"
                    aria-label="Local Vision Model"
                    value={ollamaModelInput}
                    onChange={(e) => setOllamaModelInput(e.target.value)}
                  >
                    {Array.from(
                      new Set([
                        ollamaModelInput,
                        "gemma4:e2b-it-q4_K_M",
                        "gemma3:4b",
                        ...(sysStatus?.providers?.ollama_models || []),
                      ]),
                    ).map((m) => (
                      <option key={m} value={m}>
                        {m}
                      </option>
                    ))}
                  </select>
                </div>
                {localTestRes && (
                  <div className={`test-badge ${localTestRes.ok ? "ok" : "err"}`}>
                    {localTestRes.status} · {localTestRes.model} · {localTestRes.latency_ms}ms ·{" "}
                    {localTestRes.detail}
                  </div>
                )}

                {/* Offline Desktop Library (shown cleanly in Local mode only, #179) */}
                <div className="config-section" data-testid="offline-desktop-section" style={{ marginTop: "0.35rem" }}>
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                    <strong style={{ fontSize: "0.8rem" }}>Offline Desktop Library</strong>
                    <span
                      data-testid="offline-hardware-badge"
                      style={{
                        fontFamily: "var(--font-mono)",
                        fontSize: "0.7rem",
                        color: localHardware?.offline_ready ? "#a7f3d0" : "var(--text-muted)",
                      }}
                    >
                      {localHardware
                        ? `${localHardware.offline_ready ? "Offline Ready" : "Ollama Offline"} · ${
                            localHardware.machine || localHardware.platform || "Local"
                          }${localHardware.ram_gb ? ` · ${Math.round(localHardware.ram_gb)}GB` : ""}`
                        : "Local Gemma 4"}
                    </span>
                  </div>
                  <div className="academy-row">
                    <input
                      className="input-clean"
                      aria-label="Local Video Folder"
                      placeholder="~/Videos/BJJ"
                      value={localFolderInput}
                      onChange={(e) => setLocalFolderInput(e.target.value)}
                    />
                    <button
                      type="button"
                      className="mini-btn"
                      data-testid="scan-local-folder-btn"
                      disabled={localDesktopBusy}
                      onClick={() => void handleScanLocalFolder()}
                    >
                      Scan Folder
                    </button>
                  </div>
                  {localVideos.length > 0 && (
                    <div className="academy-ref-list" data-testid="local-folder-list">
                      {localVideos.slice(0, 6).map((vid) => (
                        <div key={vid.path} className="academy-ref-item">
                          <strong>
                            {vid.filename} ({vid.size_mb} MB)
                          </strong>
                          <button
                            type="button"
                            className="mini-btn"
                            data-testid="analyze-offline-btn"
                            disabled={localDesktopBusy}
                            onClick={() => void handleAnalyzeLocalFile(vid)}
                          >
                            Analyze Offline
                          </button>
                        </div>
                      ))}
                    </div>
                  )}
                  <div className="academy-row">
                    <input
                      className="input-clean"
                      aria-label="Local Academy Notes Folder"
                      placeholder="Local academy notes folder (.md / .txt)"
                      value={localNotesFolderInput}
                      onChange={(e) => setLocalNotesFolderInput(e.target.value)}
                    />
                    <button
                      type="button"
                      className="mini-btn"
                      data-testid="import-local-academy-btn"
                      disabled={localDesktopBusy || !localNotesFolderInput.trim()}
                      onClick={() => void handleImportLocalAcademyFolder()}
                    >
                      Import Notes
                    </button>
                  </div>
                  {localDesktopMsg && (
                    <div style={{ fontSize: "0.74rem", color: "#a7f3d0" }} data-testid="local-desktop-msg">
                      {localDesktopMsg}
                    </div>
                  )}
                </div>
              </div>
            ) : (
              /* Cloud Connection Panel */
              <div className="config-section">
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                  <strong style={{ fontSize: "0.82rem" }}>Cloud Connection (Google Gemini API)</strong>
                  <button
                    type="button"
                    className="mini-btn"
                    disabled={testingTarget !== null}
                    onClick={() => void handleTestConnection("cloud")}
                  >
                    {testingTarget === "cloud" ? "Testing..." : "Test Cloud Connection"}
                  </button>
                </div>
                <div style={{ fontSize: "0.75rem", color: "var(--text-secondary)" }}>
                  Key Status:{" "}
                  <strong style={{ color: "var(--text-primary)" }}>
                    {sysStatus?.gemini_key_configured
                      ? `Configured (${sysStatus.masked_gemini_key || ".env / Secret Manager"})`
                      : "Not configured"}
                  </strong>
                </div>
                {sysStatus?.environment !== "cloud" && (
                  <input
                    type="password"
                    className="input-clean"
                    placeholder="Optional runtime GEMINI_API_KEY override (local mode)"
                    value={geminiKeyInput}
                    onChange={(e) => setGeminiKeyInput(e.target.value)}
                  />
                )}
                <div style={{ fontSize: "0.73rem", color: "var(--text-muted)", fontFamily: "var(--font-mono)" }}>
                  HUD: {sysStatus?.models?.gemma_api_model || "gemma-4-26b-a4b-it"} · Video:{" "}
                  {sysStatus?.models?.tracker_model || "gemini-4-hydrogen"} · Disputes:{" "}
                  {sysStatus?.models?.adjudicator_model || "gemini-4-neon"}
                </div>
                {cloudTestRes && (
                  <div className={`test-badge ${cloudTestRes.ok ? "ok" : "err"}`}>
                    {cloudTestRes.status} · {cloudTestRes.model} · {cloudTestRes.latency_ms}ms ·{" "}
                    {cloudTestRes.detail}
                  </div>
                )}
              </div>
            )}

            {/* Academy & Private Reference Library (#149) */}
            <div className="config-section" data-testid="academy-library-section">
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                <strong style={{ fontSize: "0.82rem" }}>Academy &amp; Reference Library</strong>
                <span style={{ fontSize: "0.72rem", color: "var(--text-muted)" }}>
                  {academyRefs.length} private note{academyRefs.length === 1 ? "" : "s"}
                </span>
              </div>
              <div className="academy-row">
                <input
                  className="input-clean"
                  aria-label="Academy Name"
                  placeholder="Your academy or school name"
                  value={academyNameInput}
                  onChange={(e) => setAcademyNameInput(e.target.value)}
                />
                <button
                  type="button"
                  className="mini-btn"
                  data-testid="save-academy-btn"
                  onClick={() => void handleSaveAcademy()}
                >
                  Save Academy
                </button>
              </div>
              <div style={{ display: "flex", gap: "0.4rem", flexWrap: "wrap" }}>
                <button
                  type="button"
                  className="filter-chip"
                  data-testid="select-osjj-academy-btn"
                  onClick={async () => {
                    const updated = await api.updateMyAcademy({
                      academy_id: "osjj-roadmap",
                      academy_name: "Open Source Jiu-Jitsu & Roadmap",
                    });
                    setAcademy(updated);
                    setAcademyNameInput(updated.academy_name || "Open Source Jiu-Jitsu & Roadmap");
                    setAcademyMsg("Switched to Open Source Jiu-Jitsu & Roadmap.");
                  }}
                >
                  Use Open Source Jiu-Jitsu &amp; Roadmap
                </button>
              </div>
              <div className="academy-ref-grid">
                <input
                  className="input-clean"
                  aria-label="Reference Title"
                  placeholder="Reference title (e.g. Knee Cut Pass)"
                  value={refTitleInput}
                  onChange={(e) => setRefTitleInput(e.target.value)}
                />
                <input
                  className="input-clean"
                  aria-label="Reference Position"
                  placeholder="Position (e.g. closed_guard)"
                  value={refPositionInput}
                  onChange={(e) => setRefPositionInput(e.target.value)}
                />
              </div>
              <div className="academy-row">
                <input
                  className="input-clean"
                  aria-label="Reference Video URL"
                  placeholder="Optional reference video URL"
                  value={refUrlInput}
                  onChange={(e) => setRefUrlInput(e.target.value)}
                />
                <button
                  type="button"
                  className="mini-btn"
                  data-testid="add-reference-btn"
                  onClick={() => void handleAddAcademyRef()}
                >
                  Add Reference
                </button>
              </div>
              <input
                className="input-clean"
                aria-label="Reference Coaching Notes"
                placeholder="Coaching notes or key grips"
                value={refNotesInput}
                onChange={(e) => setRefNotesInput(e.target.value)}
              />
              {academyMsg && (
                <div style={{ fontSize: "0.74rem", color: "#a7f3d0" }} data-testid="academy-msg">
                  {academyMsg}
                </div>
              )}
              {academyRefs.length > 0 && (
                <div className="academy-ref-list" data-testid="academy-ref-list">
                  {academyRefs.slice(0, 5).map((r) => (
                    <div key={r.id || r.doc_id || r.title} className="academy-ref-item">
                      <strong>{r.title}</strong>
                      <span>{r.position || "general"}</span>
                    </div>
                  ))}
                </div>
              )}
            </div>

            {/* Account & Save Footer */}
            <div
              style={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                gap: "0.75rem",
                flexWrap: "wrap",
              }}
            >
              <div style={{ fontSize: "0.76rem", color: "var(--text-secondary)" }}>
                Signed in as <strong style={{ color: "var(--text-primary)" }}>{user?.email}</strong>
                {configSavedMsg ? ` · ${configSavedMsg}` : ""}
                <span
                  data-testid="build-stamp-modal"
                  style={{
                    marginLeft: "0.5rem",
                    fontFamily: "var(--font-mono)",
                    fontSize: "0.68rem",
                    color: "var(--text-muted)",
                  }}
                >
                  RollMetrics v{authConfig?.app_version || "0.1.0"} · {authConfig?.build_sha || "local"}
                </span>
              </div>
              <div style={{ display: "flex", gap: "0.5rem", flexWrap: "wrap" }}>
                {user?.is_admin !== false && (
                  <button
                    type="button"
                    className="mini-btn"
                    data-testid="toggle-preview-user-btn"
                    onClick={() => setPreviewAsUser((prev) => !prev)}
                  >
                    {previewAsUser ? "Exit User View" : "View as User"}
                  </button>
                )}
                {user?.is_admin !== false && !previewAsUser && (
                  <>
                    <button
                      type="button"
                      className="mini-btn"
                      data-testid="open-admin-btn"
                      onClick={() => {
                        setSettingsOpen(false);
                        navigateViewMode("admin");
                      }}
                    >
                      App Admin -&gt;
                    </button>
                    <button
                      type="button"
                      className="mini-btn"
                      data-testid="open-ml-admin-btn"
                      onClick={() => {
                        setSettingsOpen(false);
                        navigateViewMode("ml-admin");
                      }}
                    >
                      ML Admin -&gt;
                    </button>
                  </>
                )}
                {(authConfig?.auth_required || user?.auth_mode === "google_id_token") && (
                  <button type="button" className="mini-btn" onClick={handleSignOut}>
                    Sign Out
                  </button>
                )}
                <button
                  type="button"
                  className="mini-btn"
                  onClick={() => void handleSaveConfig()}
                >
                  Save Settings
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Mobile-First Direct Upload & Google Drive Modal (#170 Workstream 2.2) */}
      {uploadModalOpen && (
        <div className="overlay-backdrop" onClick={() => setUploadModalOpen(false)}>
          <div
            className="config-modal"
            role="dialog"
            aria-label="Upload Match Video"
            data-testid="upload-match-modal"
            onClick={(e) => e.stopPropagation()}
          >
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <strong style={{ fontFamily: "var(--font-display)", fontSize: "1.02rem" }}>
                Upload Match
              </strong>
              <button
                type="button"
                className="mini-btn"
                onClick={() => setUploadModalOpen(false)}
              >
                Close
              </button>
            </div>

            <div className="rail-segmented" role="tablist" aria-label="Upload source">
              <button
                type="button"
                role="tab"
                aria-selected={uploadTab === "file"}
                className={`rail-seg-btn ${uploadTab === "file" ? "active" : ""}`}
                onClick={() => setUploadTab("file")}
              >
                Camera Roll / File
              </button>
              <button
                type="button"
                role="tab"
                aria-selected={uploadTab === "drive"}
                className={`rail-seg-btn ${uploadTab === "drive" ? "active" : ""}`}
                onClick={() => setUploadTab("drive")}
              >
                Google Drive
              </button>
            </div>

            <div className="academy-ref-grid">
              <input
                className="input-clean"
                aria-label="Match Title"
                placeholder="Match title (optional)"
                value={uploadTitle}
                onChange={(e) => setUploadTitle(e.target.value)}
              />
              <div style={{ display: "flex", gap: "0.4rem" }}>
                <select
                  aria-label="Uniform"
                  className="input-clean"
                  value={uploadUniform}
                  onChange={(e) => setUploadUniform(e.target.value as "gi" | "nogi")}
                >
                  <option value="gi">Gi</option>
                  <option value="nogi">No-Gi</option>
                </select>
                <select
                  aria-label="Belt Rank"
                  className="input-clean"
                  value={uploadBelt}
                  onChange={(e) => setUploadBelt(e.target.value as BeltRank)}
                >
                  <option value="white">White</option>
                  <option value="blue">Blue</option>
                  <option value="purple">Purple</option>
                  <option value="brown">Brown</option>
                  <option value="black">Black</option>
                </select>
              </div>
              <div style={{ display: "flex", gap: "0.4rem" }}>
                <input
                  className="input-clean"
                  aria-label="Athlete A Name"
                  placeholder="Athlete A (you)"
                  value={uploadAthleteA}
                  onChange={(e) => setUploadAthleteA(e.target.value)}
                />
                <select
                  aria-label="Athlete A Gi"
                  className="input-clean"
                  style={{ width: 88 }}
                  value={uploadGiA}
                  onChange={(e) => setUploadGiA(e.target.value as "white" | "blue" | "black")}
                >
                  <option value="white">White</option>
                  <option value="blue">Blue</option>
                  <option value="black">Black</option>
                </select>
              </div>
              <div style={{ display: "flex", gap: "0.4rem" }}>
                <input
                  className="input-clean"
                  aria-label="Athlete B Name"
                  placeholder="Opponent name"
                  value={uploadAthleteB}
                  onChange={(e) => setUploadAthleteB(e.target.value)}
                />
                <select
                  aria-label="Athlete B Gi"
                  className="input-clean"
                  style={{ width: 88 }}
                  value={uploadGiB}
                  onChange={(e) => setUploadGiB(e.target.value as "white" | "blue" | "black")}
                >
                  <option value="blue">Blue</option>
                  <option value="white">White</option>
                  <option value="black">Black</option>
                </select>
              </div>
            </div>

            {uploadTab === "file" ? (
              <label
                className="mini-btn"
                data-testid="camera-roll-upload-label"
                style={{ textAlign: "center", padding: "0.75rem", cursor: "pointer" }}
              >
                {ingestBusy ? `Uploading (${uploadProgress}%)...` : "Choose Video from Camera Roll or Files"}
                <input
                  type="file"
                  accept="video/*"
                  data-testid="camera-roll-file-input"
                  style={{ display: "none" }}
                  onChange={(e) => void handleDirectUploadFile(e.target.files?.[0] || null)}
                />
              </label>
            ) : (
              <div className="academy-row">
                <input
                  className="input-clean"
                  aria-label="Google Drive Video URL"
                  placeholder="Paste shared Google Drive link or file ID"
                  value={driveUrlInput}
                  onChange={(e) => setDriveUrlInput(e.target.value)}
                />
                <button
                  type="button"
                  className="mini-btn"
                  data-testid="import-drive-btn"
                  disabled={ingestBusy || !driveUrlInput.trim()}
                  onClick={() => void handleGoogleDriveImport()}
                >
                  {ingestBusy ? "Importing..." : "Import"}
                </button>
              </div>
            )}

            {uploadProgress > 0 && (
              <div className="upload-progress-track" data-testid="upload-progress-bar">
                <div className="upload-progress-fill" style={{ width: `${uploadProgress}%` }} />
              </div>
            )}
            {uploadError && (
              <div style={{ fontSize: "0.76rem", color: "#fca5a5" }} role="alert">
                {uploadError}
              </div>
            )}
          </div>
        </div>
      )}

      {/* Shared On-Page Reference Video Modal (Overview Home Gameplan & Film Study Scouting) */}
      {referenceModal && (
        <div className="overlay-backdrop" onClick={() => setReferenceModal(null)}>
          <div
            className="lightbox-card reference-modal-card"
            data-testid="reference-video-modal"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="lightbox-header">
              <div style={{ minWidth: 0 }}>
                <div className="lightbox-title">{referenceModal.title}</div>
                {referenceModal.subtitle && (
                  <div className="reference-modal-sub">{referenceModal.subtitle}</div>
                )}
              </div>
              <button
                type="button"
                className="mini-btn"
                data-testid="close-reference-modal-btn"
                onClick={() => setReferenceModal(null)}
              >
                Close
              </button>
            </div>
            <EmbeddedReferenceVideo
              url={referenceModal.url}
              title={referenceModal.title}
              startSec={referenceModal.startSec}
              resolveVideoUrl={(u) => (shareToken ? u : withAuthUrl(u))}
              testId="reference-modal-video"
            />
          </div>
        </div>
      )}

      {/* Clean Lightbox for Looping 0.4x Slow-Mo Highlight (#145) */}
      {mediaModal && (
        <div className="overlay-backdrop" onClick={() => setMediaModal(null)}>
          <div className="lightbox-card" data-testid="highlight-lightbox" onClick={(e) => e.stopPropagation()}>
            <div className="lightbox-header">
              <span className="lightbox-title">{mediaModal.label}</span>
              <div style={{ display: "flex", gap: "0.5rem", alignItems: "center", flexShrink: 0 }}>
                <a
                  href={mediaModal.url}
                  download
                  className="mini-btn"
                  data-testid="highlight-download-link"
                  style={{ textDecoration: "none" }}
                >
                  Download
                </a>
                <button type="button" className="mini-btn" onClick={() => setMediaModal(null)}>
                  Close
                </button>
              </div>
            </div>
            <video
              src={mediaModal.url}
              data-testid="highlight-video"
              controls
              autoPlay
              loop
              muted
              playsInline
              {...({ "webkit-playsinline": "true" } as Record<string, string>)}
            />
          </div>
        </div>
      )}
    </div>
  );
}
