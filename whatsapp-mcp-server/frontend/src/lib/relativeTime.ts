import { t } from "@/i18n/t";

const MINUTE = 60;
const HOUR = 3600;
const DAY = 86_400;

/** How long ago `then` was as of `now` (both epoch milliseconds): '5 s ago', '3 min ago', '2 h ago', '4 d ago'. */
export function relativeText(now: number, then: number): string {
  const seconds = Math.max(0, Math.floor((now - then) / 1000));
  if (seconds < MINUTE) return t("time.seconds", { n: seconds });
  if (seconds < HOUR) return t("time.minutes", { n: Math.floor(seconds / MINUTE) });
  if (seconds < DAY) return t("time.hours", { n: Math.floor(seconds / HOUR) });
  return t("time.days", { n: Math.floor(seconds / DAY) });
}

/** The exact moment in UTC to the second, '2026-11-07 21:44:03 UTC'; empty for a value that is not a moment. */
export function exactUtc(moment: number): string {
  if (!Number.isFinite(moment)) return "";
  return `${new Date(moment).toISOString().slice(0, 19).replace("T", " ")} UTC`;
}
