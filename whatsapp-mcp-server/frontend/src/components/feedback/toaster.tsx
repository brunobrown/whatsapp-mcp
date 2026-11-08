import { useEffect } from "react";
import type { CSSProperties } from "react";
import { Toaster as Sonner, toast } from "sonner";
import { useT } from "@/i18n/t";
import { raiseNotice } from "@/lib/notices";
import type { NoticeKind, NoticeParams } from "@/lib/notices";
import { useUiStore } from "@/stores/ui";
import type { Notice } from "@/stores/ui";

type Tone = "success" | "warning" | "error";
const TONES: Record<NoticeKind, Tone> = {
  saved: "success",
  copied: "success",
  copyFailed: "error",
  sessionWarning: "warning",
  saveFailed: "error",
  bridgeUnavailable: "warning",
  refused: "error",
  connectionLost: "error",
  sessionEnded: "error",
};

/** How far above the bottom edge the toasts float, in px: over the save bar's height on a desktop and on a phone. */
export const SAVE_BAR_CLEARANCE = 88;
export const SAVE_BAR_CLEARANCE_PHONE = 136;

const sonnerTheme: CSSProperties = {
  "--normal-bg": "var(--panel)",
  "--normal-text": "var(--ink)",
  "--normal-border": "var(--line)",
} as CSSProperties;

let pendingAsk: Promise<NotificationPermission> | null = null;

function nativeSupported(): boolean {
  return typeof Notification !== "undefined";
}

/**
 * Asks the browser for permission to show native notifications. Call it from the operator's first Save, never
 * on load. It asks only while the browser has not decided.
 */
async function askPermission(): Promise<NotificationPermission | "unsupported"> {
  if (!nativeSupported()) return "unsupported";
  if (Notification.permission !== "default") return Notification.permission;
  // The browser remembers the answer, so a later call sees a decided permission. Concurrent calls share one prompt.
  pendingAsk ??= Notification.requestPermission()
    .catch(() => Notification.permission)
    .finally(() => {
      pendingAsk = null;
    });
  return pendingAsk;
}

/** A native notification helps when the tab is in the background; in view, a toast is enough. */
function tryNative(notice: Notice, appName: string): boolean {
  if (!nativeSupported() || Notification.permission !== "granted" || !document.hidden) return false;
  try {
    new Notification(appName, { body: notice.text, tag: notice.key });
    return true;
  } catch {
    return false;
  }
}

function deliver(notice: Notice, appName: string): void {
  if (tryNative(notice, appName)) return;
  const tone = TONES[notice.key as NoticeKind] as Tone | undefined;
  const show = tone === undefined ? toast : toast[tone];
  show(notice.text, { id: notice.id });
}

/** Hosts the toasts and delivers the notices the store queues, once each. Mount it once, near the root. */
export function Toaster() {
  const t = useT();
  const theme = useUiStore((state) => state.theme);
  const notices = useUiStore((state) => state.notices);
  const dismissNotice = useUiStore((state) => state.dismissNotice);

  useEffect(() => {
    for (const notice of notices) {
      deliver(notice, t("app.name"));
      dismissNotice(notice.id);
    }
  }, [notices, dismissNotice, t]);

  return (
    <Sonner
      theme={theme}
      position="bottom-right"
      // Clear of the save bar, which sits at the bottom edge (two rows high on a phone): a toast never covers Save.
      offset={{ bottom: SAVE_BAR_CLEARANCE }}
      mobileOffset={{ bottom: SAVE_BAR_CLEARANCE_PHONE }}
      closeButton
      customAriaLabel={t("toast.region")}
      toastOptions={{ closeButtonAriaLabel: t("common.close") }}
      style={sonnerTheme}
    />
  );
}

/** `notify` queues a global notice (a repeat within 60 s is skipped); `requestPermission` is for the first Save. */
export function useNotify(): {
  notify: (kind: NoticeKind, params?: NoticeParams) => void;
  requestPermission: () => Promise<NotificationPermission | "unsupported">;
} {
  return {
    notify: (kind, params) => {
      raiseNotice(kind, params);
    },
    requestPermission: askPermission,
  };
}
