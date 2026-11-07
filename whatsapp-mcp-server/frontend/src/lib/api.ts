import axios, { isAxiosError } from "axios";
import { t } from "@/i18n/t";
import type { ServerErrorCode, SessionInfo } from "@/types/api";
import { queryClient, SESSION_KEY } from "./queryClient";

export type ApiErrorCode =
  | ServerErrorCode
  | "throttled"
  | "invalid"
  | "conflict"
  | "needs_confirmation"
  | "network"
  | "timeout"
  | "unexpected";

const SERVER_CODES: ReadonlySet<string> = new Set<ServerErrorCode>([
  "bad_host",
  "origin",
  "csrf",
  "content_type",
  "unauthenticated",
  "session_expired",
  "not_found",
  "bad_request",
]);

/** Every failure of a call, whatever its source, in one shape. `status` is 0 when no answer arrived. */
export class ApiError extends Error {
  readonly status: number;
  readonly code: ApiErrorCode;
  /** Seconds to wait before trying again (HTTP 429). */
  readonly wait: number | undefined;
  /** Message per field key (HTTP 422). */
  readonly fields: Record<string, string> | undefined;
  /** The answer body, for callers that read a save outcome from a 409 or 422. */
  readonly data: unknown;

  constructor(init: {
    status: number;
    code: ApiErrorCode;
    message: string;
    wait?: number;
    fields?: Record<string, string>;
    data?: unknown;
  }) {
    super(init.message);
    this.name = "ApiError";
    this.status = init.status;
    this.code = init.code;
    this.wait = init.wait;
    this.fields = init.fields;
    this.data = init.data;
  }
}

export function isApiError(value: unknown): value is ApiError {
  return value instanceof ApiError;
}

type Body = Readonly<Record<string, unknown>>;

function isBody(value: unknown): value is Body {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function textOf(value: unknown): string | undefined {
  return typeof value === "string" && value !== "" ? value : undefined;
}

function stringMap(value: unknown): Record<string, string> | undefined {
  if (!isBody(value)) return undefined;
  const entries = Object.entries(value).filter((entry): entry is [string, string] => typeof entry[1] === "string");
  return Object.fromEntries(entries);
}

function fromResponse(status: number, data: unknown): ApiError {
  const body = isBody(data) ? data : undefined;
  const message = textOf(body?.["message"]);
  const code = textOf(body?.["code"]);

  if (code !== undefined && SERVER_CODES.has(code)) {
    return new ApiError({
      status,
      code: code as ServerErrorCode,
      message: message ?? t("error.unexpected"),
      data,
    });
  }
  if (status === 429) {
    const wait = typeof body?.["wait"] === "number" ? body["wait"] : undefined;
    const fallback = wait === undefined ? t("error.unexpected") : t("auth.wait", { seconds: wait });
    return new ApiError({ status, code: "throttled", message: message ?? fallback, ...(wait !== undefined && { wait }), data });
  }
  if (status === 422) {
    const general = Array.isArray(body?.["general"]) ? textOf(body["general"][0]) : undefined;
    const fields = stringMap(body?.["errors"]);
    return new ApiError({
      status,
      code: "invalid",
      message: message ?? general ?? t("error.invalid"),
      ...(fields !== undefined && { fields }),
      data,
    });
  }
  if (status === 409) {
    const confirm = body?.["needs_confirmation"] === true && body["conflict"] !== true;
    return new ApiError({
      status,
      code: confirm ? "needs_confirmation" : "conflict",
      message: message ?? t("error.unexpected"),
      data,
    });
  }
  return new ApiError({ status, code: "unexpected", message: message ?? t("error.unexpected"), data });
}

/** Turns anything an Axios call can throw into an `ApiError`. */
export function normalizeError(error: unknown): ApiError {
  if (isApiError(error)) return error;
  if (!isAxiosError(error)) return new ApiError({ status: 0, code: "unexpected", message: t("error.unexpected") });
  if (error.response) return fromResponse(error.response.status, error.response.data);
  if (error.code === "ECONNABORTED" || error.code === "ETIMEDOUT") {
    return new ApiError({ status: 0, code: "timeout", message: t("error.timeout") });
  }
  return new ApiError({ status: 0, code: "network", message: t("error.network") });
}

type UnauthorizedListener = (error: ApiError) => void;
const listeners = new Set<UnauthorizedListener>();

/** Calls `listener` whenever the server says the session is gone. Returns the function that stops it. */
export function onUnauthorized(listener: UnauthorizedListener): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

const WRITES: ReadonlySet<string> = new Set(["post", "put", "patch", "delete"]);

export const api = axios.create({ baseURL: "/api", timeout: 10_000, withCredentials: true });

api.interceptors.request.use((config) => {
  if (WRITES.has((config.method ?? "get").toLowerCase())) {
    const csrf = queryClient.getQueryData<SessionInfo>(SESSION_KEY)?.csrf;
    if (csrf) config.headers.set("X-CSRF-Token", csrf);
  }
  return config;
});

api.interceptors.response.use(undefined, (error: unknown) => {
  const failure = normalizeError(error);
  if (failure.status === 401) {
    queryClient.clear();
    for (const listener of [...listeners]) listener(failure);
  }
  return Promise.reject(failure);
});
