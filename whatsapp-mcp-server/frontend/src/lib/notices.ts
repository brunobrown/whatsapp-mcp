import { t } from "@/i18n/t";
import type { TextKey } from "@/i18n/t";
import { useUiStore } from "@/stores/ui";
import type { Notice } from "@/stores/ui";

/** Each global notice kind and the catalog key of its fixed sentence. */
const NOTICE_TEXTS = {
  saved: "notice.saved",
  copied: "notice.copied",
  copyFailed: "notice.copyFailed",
  saveFailed: "notice.saveFailed",
  bridgeUnavailable: "notice.bridgeUnavailable",
  refused: "notice.refused",
  connectionLost: "notice.connectionLost",
  sessionWarning: "notice.sessionWarning",
  sessionEnded: "notice.sessionEnded",
} as const satisfies Record<string, TextKey>;

export type NoticeKind = keyof typeof NOTICE_TEXTS;
/** Numbers only: a notice never carries text that came from a form. */
export type NoticeParams = Readonly<Record<string, number>>;

export const REPEAT_WINDOW_MS = 60_000;

const lastShown = new Map<NoticeKind, number>();

function numbersOnly(params: NoticeParams | undefined): NoticeParams | undefined {
  if (!params) return undefined;
  return Object.fromEntries(Object.entries(params).filter(([, value]) => typeof value === "number" && Number.isFinite(value)));
}

export function noticeText(kind: NoticeKind, params?: NoticeParams): string {
  return t(NOTICE_TEXTS[kind], numbersOnly(params));
}

/**
 * Queues a global notice, unless the same kind was shown less than 60 seconds ago.
 * Returns the queued notice, or null when the repeat was skipped.
 */
export function raiseNotice(kind: NoticeKind, params?: NoticeParams, now: number = Date.now()): Notice | null {
  const previous = lastShown.get(kind);
  if (previous !== undefined && now - previous < REPEAT_WINDOW_MS) return null;
  lastShown.set(kind, now);
  useUiStore.getState().pushNotice({ key: kind, text: noticeText(kind, params), at: now });
  const queue = useUiStore.getState().notices;
  return queue[queue.length - 1] ?? null;
}

/**
 * Queues a notice that answers something the operator just did, so it is never held back as a repeat: each copy
 * says Copied, even two in a row.
 */
export function showNotice(kind: NoticeKind, params?: NoticeParams, now: number = Date.now()): void {
  useUiStore.getState().pushNotice({ key: kind, text: noticeText(kind, params), at: now });
}

/** Forgets what was shown; for tests. */
export function resetNoticeMemory(): void {
  lastShown.clear();
}
