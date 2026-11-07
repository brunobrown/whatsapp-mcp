import { QueryClient } from "@tanstack/react-query";

/** Query key of the session; its data is a `SessionInfo`, whose `csrf` the HTTP client sends on writes. */
export const SESSION_KEY = ["session"] as const;

export const queryClient = new QueryClient();
