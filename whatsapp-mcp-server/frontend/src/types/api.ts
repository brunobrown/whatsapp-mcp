/** Contracts of the interface API, as the Python server sends and expects them. */

export type SessionState = "setup" | "login" | "broken" | "authenticated";

export interface SessionInfo {
  state: SessionState;
  csrf: string;
  idle_remaining?: number;
  message?: string;
}

export interface CodeResult {
  announced: boolean;
  message?: string;
}

/** Answer of setup, login and password change. A refusal is `ok: false` (HTTP 200); a wait is HTTP 429 with `wait`. */
export interface ActionResult {
  ok: boolean;
  message?: string;
  csrf?: string;
  wait?: number;
}

export interface SetupRequest {
  code: string;
  password: string;
  confirm: string;
}

export interface LoginRequest {
  password: string;
}

export interface PasswordChangeRequest {
  current: string;
  new: string;
  confirm: string;
}

export type RestartScope = "bridge" | "server";

export interface SectionSummary {
  id: string;
  title: string;
  icon: string;
  order: number;
  kind: "section" | "page";
  description: string;
  restart: RestartScope | null;
  /** A value saved here waits for a restart; the server knows it, so the mark survives a reload. */
  pending: boolean;
}

export type FieldKind = "bool" | "int" | "float" | "text" | "choice" | "multichoice" | "secret" | "list" | "records";

export interface FieldSchema {
  key: string;
  label: string;
  kind: FieldKind;
  default: unknown;
  help: string;
  secret: boolean;
  env_var: string | null;
  options: string[];
  bounds: [number, number] | null;
  max_length: number;
  max_items: number;
  multiline: boolean;
  fields: FieldSchema[];
  id_key: string | null;
  readonly: boolean;
  risk: string | null;
  risk_when: unknown[];
  group?: string;
}

export type Origin = "default" | "file" | "environment";

export interface FieldState {
  origin: Origin;
  locked: boolean;
  pending: RestartScope | null;
}

/** A secret leaves the server only as whether it is set. */
export interface SecretValue {
  set: boolean;
}

export interface SectionView {
  id: string;
  title: string;
  description: string;
  restart: RestartScope | null;
  status: "ready" | "empty" | "error" | "unavailable";
  banner: string | null;
  warnings: string[];
  version: string;
  read_only: boolean;
  fields: FieldSchema[];
  values: Record<string, unknown>;
  state: Record<string, FieldState>;
}

/** A secret change: a new string, keep (`{set: true}`) or clear (`{clear: true}`). */
export type SecretChange = string | { set: true } | { clear: true };

export interface SaveRequest {
  version: string;
  changes: Record<string, unknown>;
  restore?: string[];
  confirm_replace?: boolean;
}

export interface ValidateResult {
  errors: Record<string, string>;
  general: string[];
  can_save: boolean;
}

export interface SaveOutcome {
  ok: boolean;
  unchanged: boolean;
  conflict: boolean;
  needs_confirmation: boolean;
  errors: Record<string, string>;
  general: string[];
  restarts: RestartScope[];
}

export interface WebhookEndpointStatus {
  name?: string;
  delivered?: number;
  failed?: number;
  dropped?: number;
  queued?: number;
  last_error?: string;
  last_success_at?: string | null;
  persistent?: boolean;
  recovered?: number;
}

export interface BridgeStatus {
  connected?: boolean;
  logged_in?: boolean;
  phone?: string;
  version?: string;
  started_at?: string;
  uptime_seconds?: number;
  last_event_at?: string | null;
  last_message_at?: string | null;
  last_connected_at?: string | null;
  last_disconnected_at?: string | null;
  pending_media_retries?: number;
  webhooks?: { enabled: boolean; error?: string; endpoints: WebhookEndpointStatus[] };
}

export interface Unavailable {
  unavailable: string;
  message: string;
}

export type OpsStatus = { status: BridgeStatus } | Unavailable;

export interface OpsCounters {
  window_hours: number;
  since: string;
  messages: { received?: number; from_this_account?: number } | null;
  monitor_replies: { by_profile: Record<string, Record<string, number>>; error_codes: Record<string, number> };
  sends: { by_result: Record<string, number>; with_override: number };
  pending_drafts: number;
  webhooks: {
    enabled?: boolean;
    endpoints?: number;
    delivered?: number;
    failed?: number;
    dropped?: number;
    queued?: number;
  } | null;
}

export type OpsCountersResult = OpsCounters | Unavailable;

export interface DraftRow {
  id?: string;
  profile?: string;
  chat_jid?: string;
  message_id?: string;
  sender?: string;
  created_at?: string;
  expires_at?: string;
}

export interface DraftsResult {
  rows: DraftRow[];
  count: number;
}

export type AuditKind = "send" | "monitor";

export interface AuditRow {
  ts?: string;
  kind?: string;
  profile?: string;
  chat_jid?: string | null;
  chat_name?: string | null;
  sender?: string;
  message_id?: string;
  triggers?: string[];
  mode?: string;
  approval_used?: boolean;
  override?: boolean;
  result?: string;
  rule?: string;
  size?: number;
  error_code?: string;
  reply_id?: string;
}

export interface AuditResult {
  rows: AuditRow[];
}

/** Error codes the security layer and the routes send in `{code, message}`. */
export type ServerErrorCode =
  | "bad_host"
  | "origin"
  | "csrf"
  | "content_type"
  | "unauthenticated"
  | "session_expired"
  | "not_found"
  | "bad_request";
