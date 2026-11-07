import { useQuery } from "@tanstack/react-query";
import { api, isApiError } from "@/lib/api";
import { queryClient, SESSION_KEY } from "@/lib/queryClient";
import type {
  ActionResult,
  AuditKind,
  AuditResult,
  CodeResult,
  DraftsResult,
  LoginRequest,
  OpsCountersResult,
  OpsStatus,
  PasswordChangeRequest,
  SaveOutcome,
  SaveRequest,
  SectionSummary,
  SectionView,
  SessionInfo,
  SetupRequest,
  ValidateResult,
} from "@/types/api";

/** Query keys, one place, so a mutation and a hook cannot drift apart. */
export const keys = {
  session: SESSION_KEY,
  sections: ["sections"] as const,
  section: (id: string) => ["section", id] as const,
  ops: (name: "status" | "counters" | "drafts") => ["ops", name] as const,
  audit: (kind: AuditKind) => ["ops", "audit", kind] as const,
};

export const OPS_POLL_MS = 5000;

/** Polls and session checks must not keep a session alive: the server skips the idle reset for these. */
const BACKGROUND = { headers: { "X-Background": "1" } } as const;

/** After a sign-in the cache holds the fresh csrf, which the next write sends. */
function rememberSignedIn(result: ActionResult): void {
  if (result.ok && result.csrf) {
    queryClient.setQueryData<SessionInfo>(SESSION_KEY, { state: "authenticated", csrf: result.csrf });
  }
}

export const sessionService = {
  async get(options: { background?: boolean } = {}): Promise<SessionInfo> {
    const { data } = await api.get<SessionInfo>("/session", options.background ? BACKGROUND : undefined);
    queryClient.setQueryData(SESSION_KEY, data);
    return data;
  },
  async requestCode(): Promise<CodeResult> {
    const { data } = await api.post<CodeResult>("/session/code", {});
    return data;
  },
  async setup(request: SetupRequest): Promise<ActionResult> {
    const { data } = await api.post<ActionResult>("/session/setup", request);
    rememberSignedIn(data);
    return data;
  },
  async login(request: LoginRequest): Promise<ActionResult> {
    const { data } = await api.post<ActionResult>("/session/login", request);
    rememberSignedIn(data);
    return data;
  },
  async logout(): Promise<void> {
    try {
      await api.delete("/session");
    } finally {
      queryClient.clear();
    }
  },
  async changePassword(request: PasswordChangeRequest): Promise<ActionResult> {
    const { data } = await api.post<ActionResult>("/account/password", request);
    return data;
  },
};

function isOutcome(value: unknown): value is SaveOutcome {
  return typeof value === "object" && value !== null && "ok" in value;
}

export const sectionsService = {
  async list(): Promise<SectionSummary[]> {
    return (await api.get<SectionSummary[]>("/sections")).data;
  },
  async get(id: string): Promise<SectionView> {
    return (await api.get<SectionView>(`/sections/${encodeURIComponent(id)}`)).data;
  },
  async validate(id: string, request: SaveRequest): Promise<ValidateResult> {
    return (await api.post<ValidateResult>(`/sections/${encodeURIComponent(id)}/validate`, request)).data;
  },
  /** A refusal (409 conflict or confirmation, 422 field errors) is an outcome to show, so it is returned, not thrown. */
  async save(id: string, request: SaveRequest): Promise<SaveOutcome> {
    try {
      return (await api.put<SaveOutcome>(`/sections/${encodeURIComponent(id)}`, request)).data;
    } catch (error) {
      if (isApiError(error) && (error.status === 409 || error.status === 422) && isOutcome(error.data)) {
        return error.data;
      }
      throw error;
    }
  },
};

export const opsService = {
  async status(): Promise<OpsStatus> {
    return (await api.get<OpsStatus>("/ops/status", BACKGROUND)).data;
  },
  async counters(): Promise<OpsCountersResult> {
    return (await api.get<OpsCountersResult>("/ops/counters", BACKGROUND)).data;
  },
  async drafts(): Promise<DraftsResult> {
    return (await api.get<DraftsResult>("/ops/drafts", BACKGROUND)).data;
  },
  async audit(kind: AuditKind): Promise<AuditResult> {
    return (await api.get<AuditResult>(`/ops/audit/${kind}`, BACKGROUND)).data;
  },
};

/** Poll only while the tab is visible. */
function pollWhileVisible(): number | false {
  return document.visibilityState === "visible" ? OPS_POLL_MS : false;
}

const opsPolling = {
  refetchInterval: pollWhileVisible,
  refetchIntervalInBackground: false,
  staleTime: 0,
} as const;

export function useSession() {
  return useQuery({ queryKey: keys.session, queryFn: () => sessionService.get(), retry: false, staleTime: 30_000 });
}

export function useSections() {
  return useQuery({ queryKey: keys.sections, queryFn: () => sectionsService.list() });
}

/** Staleness 0 and a refetch on focus: a file edited elsewhere shows up when the operator returns. */
export function useSection(id: string) {
  return useQuery({
    queryKey: keys.section(id),
    queryFn: () => sectionsService.get(id),
    staleTime: 0,
    refetchOnWindowFocus: true,
  });
}

export function useOpsStatus() {
  return useQuery({ queryKey: keys.ops("status"), queryFn: () => opsService.status(), ...opsPolling });
}

export function useOpsCounters() {
  return useQuery({ queryKey: keys.ops("counters"), queryFn: () => opsService.counters(), ...opsPolling });
}

export function useOpsDrafts() {
  return useQuery({ queryKey: keys.ops("drafts"), queryFn: () => opsService.drafts(), ...opsPolling });
}

export function useOpsAudit(kind: AuditKind) {
  return useQuery({ queryKey: keys.audit(kind), queryFn: () => opsService.audit(kind), ...opsPolling });
}
