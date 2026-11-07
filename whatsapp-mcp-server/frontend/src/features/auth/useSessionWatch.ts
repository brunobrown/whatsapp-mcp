import { useNavigate } from "@tanstack/react-router";
import { useEffect } from "react";
import { onUnauthorized } from "@/lib/api";
import { raiseNotice } from "@/lib/notices";
import { queryClient, SESSION_KEY } from "@/lib/queryClient";
import { sessionService } from "@/services/services";

export const SESSION_POLL_MS = 15_000;
/** The server's idle limit is shown to the operator this many seconds before it lapses. */
export const WARN_AT_SECONDS = 120;

/**
 * Watches an open session from inside the interface. A background read (it does not renew the session)
 * tells how long the idle time has left: at 120 seconds a warning shows once, and when the server no longer
 * knows the session the cache is cleared and the operator is sent to sign-in with the inactivity message.
 * A 401 from any other call that says the session expired ends it the same way.
 */
export function useSessionWatch(): void {
  const navigate = useNavigate();

  useEffect(() => {
    let warned = false;
    let stopped = false;

    const end = (message?: string): void => {
      if (stopped) return;
      stopped = true;
      raiseNotice("sessionEnded");
      void navigate({ to: "/login", replace: true });
      if (message !== undefined) {
        // The sign-in page reads its message from the cached session.
        queryClient.setQueryData(SESSION_KEY, { state: "login", csrf: "", message });
      }
    };

    const poll = async (): Promise<void> => {
      try {
        const session = await sessionService.get({ background: true });
        if (stopped) return;
        if (session.state !== "authenticated") {
          queryClient.clear();
          end(session.message);
          return;
        }
        const left = session.idle_remaining;
        if (left === undefined) return;
        if (left > WARN_AT_SECONDS) {
          warned = false;
        } else if (!warned) {
          warned = true;
          raiseNotice("sessionWarning", { minutes: Math.max(1, Math.ceil(left / 60)) });
        }
      } catch {
        // A lost connection or a 401 is handled by the HTTP client; the next poll tries again.
      }
    };

    const timer = setInterval(() => {
      void poll();
    }, SESSION_POLL_MS);
    const stopListening = onUnauthorized((error) => {
      if (error.code === "session_expired") end(error.message);
    });
    return () => {
      stopped = true;
      clearInterval(timer);
      stopListening();
    };
  }, [navigate]);
}
