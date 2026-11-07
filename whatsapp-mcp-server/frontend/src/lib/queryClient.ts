import { QueryClient } from "@tanstack/react-query";

/** Query key of the session; its data is a `SessionInfo`, whose `csrf` the HTTP client sends on writes. */
export const SESSION_KEY = ["session"] as const;

/** A refused request (4xx) will be refused again; only a failure to answer is worth one more try. */
function retryOnce(count: number, error: unknown): boolean {
  const status = (error as { status?: unknown } | null)?.status;
  return count < 1 && !(typeof status === "number" && status >= 400 && status < 500);
}

export const queryClient = new QueryClient({ defaultOptions: { queries: { retry: retryOnce } } });
