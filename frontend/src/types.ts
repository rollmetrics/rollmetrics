export type BeltRank = "white" | "blue" | "purple" | "brown" | "black";
export type ModelProfile = "cloud" | "local";
export type RulesetId = "ibjjf_v6" | "adcc" | "sparring";

export interface AthleteAnchorPoint {
  x: number;
  y: number;
}

export interface PinnedDrill {
  title: string;
  url: string;
  note?: string;
}

export interface UploadJobRecord {
  job_id: string;
  filename: string;
  owner_email: string;
  status: string;
  ruleset: RulesetId;
  created_at: string;
  match_id?: string;
  detail?: string;
}

export interface IdLockSummary {
  id_lock_pct: number;
  evaluated_bouts: number;
  swap_count: number;
  matching_gi_bouts?: number;
}

export interface TeamRosterItem {
  match_id: string;
  title: string;
  owner_email: string;
  athlete_name: string;
  belt_label: string;
  ruleset: RulesetId;
  review_status: "needs_review" | "reviewed";
  pinned_drills: PinnedDrill[];
  created_at: string;
}

export interface RuleInfo {
  article: string;
  title: string;
  excerpt: string;
  rulebook_url: string;
  ibjjf_video_url?: string;
  ibjjf_video_title?: string;
}

export interface CtaInfo {
  title?: string;
  excerpt?: string;
  video_url?: string;
  start_sec?: number;
  source_kind?: string;
}

export interface ShowcaseEvent {
  id: string;
  video_sec: number;
  time_str: string;
  clock_str: string;
  actor: "A" | "B";
  call: string;
  detail: string;
  rule: string;
  rule_info?: RuleInfo;
  cta_info?: CtaInfo;
  cta_url: string;
  pts_a: number;
  adv_a: number;
  pen_a: number;
  pts_b: number;
  adv_b: number;
  pen_b: number;
  control_3s?: boolean;
  hold_sec?: number;
  is_finish?: boolean;
  coach_note?: string;
  edited_by?: string;
}

export interface ShowcaseAthlete {
  name: string;
  belt: BeltRank;
  gi: "white" | "blue" | "black";
}

export interface CoachCtaLink {
  label: string;
  url: string;
}

export interface CoachAthleteAdvice {
  name: string;
  gi: "white" | "blue" | "black";
  cues: string[];
  cta_links: CoachCtaLink[];
}

export interface CoachSummary {
  overview: string;
  athlete_a: CoachAthleteAdvice;
  athlete_b: CoachAthleteAdvice;
}

export type MatchBucket = "my_matches" | "film_study";

export interface PersonalCoachingSummary {
  user_email: string;
  match_count: number;
  record: {
    wins: number;
    losses: number;
    draws: number;
    subs: number;
    decisions: number;
    label: string;
  };
  points_scored: number;
  points_allowed: number;
  recurring_positions: string[];
  summary_line: string;
  top_cta_priorities: Array<{
    title: string;
    cue: string;
    url: string;
  }>;
}

export interface FilmStudySummary {
  focus_athlete: string;
  match_count: number;
  record_label: string;
  combined_points_for: number;
  combined_points_against: number;
  summary_text: string;
  a_game_patterns: string[];
  coaching_takeaways: string[];
  cta_links: Array<{ title: string; url: string }>;
  match_ids: string[];
}

export interface AdminScoreboardModelRow {
  model_id: string;
  match_count: number;
  winner_accuracy: number;
  final_score_exact_match: number;
  mae_pts: number;
  mae_adv: number;
  mae_pen: number;
  score_event_f1: number;
  p50_ms: number;
  cost_usd: number;
}

export interface AdminOverviewResponse {
  environment: string;
  app_version: string;
  build_sha: string;
  scoreboard_eval: {
    benchmark_id: string;
    evaluated_at: string;
    total_matches: number;
    calibration: number;
    heldout: number;
    leakage_gate: string;
    splits: {
      calibration: AdminScoreboardModelRow[];
      heldout: AdminScoreboardModelRow[];
    };
  };
  knowledge_eval: {
    version: string;
    total_docs: number;
    ibjjf_docs: number;
    cta_docs: number;
    suites: {
      ibjjf_rules: string;
      match_timelines: string;
      cta_recall_at_3: string;
    };
  };
  users_and_usage: Array<{
    email: string;
    name?: string;
    role?: "admin" | "coach" | "member" | "demo";
    teams?: string[];
    academy_name?: string;
    my_matches: number;
    film_study: number;
    uploaded_videos: number;
    analyses_run: number;
    live_sessions: number;
    last_seen: string;
  }>;
  athletes?: Array<{
    email: string;
    name?: string;
    role?: "admin" | "coach" | "member" | "demo";
    teams?: string[];
    academy_name?: string;
    my_matches: number;
    film_study: number;
    uploaded_videos: number;
    analyses_run: number;
    live_sessions: number;
    last_seen: string;
  }>;
  teams?: Array<{
    id: string;
    name: string;
    member_count: number;
    default_curriculum: string;
    default_ruleset: string;
  }>;
  curricula?: Array<{
    id: string;
    name: string;
    visibility: string;
    doc_count: number;
    video_backed_count: number;
    text_only_count: number;
    status: string;
  }>;
  rulesets?: Array<{
    id: string;
    name: string;
    status: string;
    legality_cases: number;
    rule_docs: number;
    official_videos: number;
    notes: string;
  }>;
  engine_versions?: Array<{
    version: string;
    name: string;
    harness: string;
    heldout_winner: string;
    notes: string;
  }>;
  experiments?: Array<{
    id: string;
    title: string;
    delta: string;
    status: string;
  }>;
  dataset_details?: {
    benchmark_id: string;
    total_matches: number;
    calibration_matches: number;
    heldout_matches: number;
    cross_split_video_overlap: number;
    leakage_control: string;
    early_stoppage_audit: string;
    vicos_adapter: string;
  };
  upload_jobs?: UploadJobRecord[];
  id_lock_eval?: IdLockSummary;
  daily_cloud_spend_usd?: number;
  max_daily_cloud_usd?: number;
}

export interface ShowcaseMatch {
  id: string;
  pill: string;
  title: string;
  belt: BeltRank;
  belt_label: string;
  ruleset?: RulesetId;
  bucket?: MatchBucket;
  owner_email?: string;
  review_status?: "needs_review" | "reviewed";
  pinned_drills?: PinnedDrill[];
  athlete_a_anchor?: AthleteAnchorPoint;
  athlete_b_anchor?: AthleteAnchorPoint;
  trim_start_sec?: number;
  trim_end_sec?: number;
  regulation_sec: number;
  video_duration_sec: number;
  video_start_sec: number;
  video_end_sec: number;
  clock_anchors?: Array<[number, number]>;
  athlete_a: ShowcaseAthlete;
  athlete_b: ShowcaseAthlete;
  winner: "A" | "B" | "draw";
  win_label: string;
  video_url?: string;
  poster_url?: string;
  video_path?: string;
  coach_summary?: CoachSummary;
  events: ShowcaseEvent[];
}

export interface AuthConfig {
  environment: "local" | "cloud" | "test";
  google_client_id: string;
  allow_local_dev_bypass: boolean;
  allow_open_signup?: boolean;
  auth_required: boolean;
  public_url: string;
  default_profile: ModelProfile;
  app_version?: string;
  build_sha?: string;
}

export interface AuthUser {
  email: string;
  name: string;
  picture: string;
  belt?: BeltRank;
  academy?: string;
  coach?: string;
  auth_mode: string;
  role?: "admin" | "coach" | "member" | "demo";
  teams?: string[];
  is_admin?: boolean;
  academy_id?: string;
  academy_name?: string;
}

export interface SystemStatusResponse {
  environment: string;
  active_profile: ModelProfile;
  ollama_base_url: string;
  gemini_key_configured: boolean;
  masked_gemini_key: string;
  models: {
    tracker_model: string;
    adjudicator_model: string;
    gemma_api_model: string;
    ollama_vision_model: string;
  };
  providers: {
    cloud_gemini_configured: boolean;
    ollama_reachable: boolean;
    ollama_models: string[];
    recommended_local_model: string;
  };
}

export interface ConnectionTestResult {
  ok: boolean;
  target: "local" | "cloud";
  status: string;
  model: string;
  ollama_models?: string[];
  latency_ms: number;
  detail: string;
}

export interface AcademyProfile {
  academy_id: string;
  academy_name: string;
  reference_count?: number;
}

export interface AcademyReference {
  id?: string;
  doc_id?: string;
  title: string;
  position?: string;
  notes?: string;
  text?: string;
  source_url?: string;
  url?: string;
  belt?: string;
  created_at?: string;
}

export interface UploadInitResponse {
  upload_id: string;
  upload_strategy: "gcs_signed_put" | "multipart";
  upload_url: string;
  method: "PUT" | "POST";
  headers: Record<string, string>;
  gcs_object: string;
}

export interface ShareLinkResponse {
  token: string;
  match_id: string;
  share_path: string;
  share_url: string;
}

export interface SharedMatchPayload {
  token: string;
  academy_name: string;
  match: ShowcaseMatch;
}

export interface LocalHardwareStatus {
  offline_ready: boolean;
  ollama_reachable: boolean;
  installed_models: string[];
  recommended_model: string;
  platform?: string;
  machine?: string;
  ram_gb?: number;
}

export interface LocalVideoFile {
  path: string;
  filename: string;
  size_mb: number;
  duration_sec?: number;
  analyzed?: boolean;
}

export interface LocalFolderScanResponse {
  folder_path: string;
  count: number;
  videos: LocalVideoFile[];
}

export interface LocalAcademyImportResponse {
  folder_path: string;
  academy_id: string;
  academy_name: string;
  imported_count: number;
}
