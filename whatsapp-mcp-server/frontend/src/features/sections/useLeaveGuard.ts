import { useBlocker } from "@tanstack/react-router";
import { useEffect } from "react";
import { useUiStore } from "@/stores/ui";

/** The page the session ends on. Losing the session must never wait for an answer about a draft. */
const SIGN_IN_PATH = "/login";

/**
 * Stops the operator from leaving with unsaved changes: a navigation inside the interface waits for an
 * answer (`stay` or `leave`), and closing or reloading the tab gets the browser's own prompt. A clean form
 * leaves without asking.
 */
export function useLeaveGuard(dirty: boolean): { open: boolean; stay: () => void; leave: () => void } {
  const blocker = useBlocker({
    shouldBlockFn: ({ next }) => dirty && next.pathname !== SIGN_IN_PATH,
    enableBeforeUnload: () => dirty,
    disabled: !dirty,
    withResolver: true,
  });
  const open = blocker.status === "blocked";
  // The menu drawer waits for the answer before it closes, so it needs to know when the question is on screen.
  useEffect(() => {
    useUiStore.getState().setLeaveAsking(open);
    return () => {
      useUiStore.getState().setLeaveAsking(false);
    };
  }, [open]);
  return {
    open,
    stay: () => {
      blocker.reset?.();
    },
    leave: () => {
      blocker.proceed?.();
    },
  };
}
