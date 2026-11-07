import { t } from "@/i18n/t";

const LOCALE = "en";
const MINUTE = 60;
const HOUR = 3600;
const DAY = 86_400;

const momentFormat = new Intl.DateTimeFormat(LOCALE, {
  year: "numeric",
  month: "short",
  day: "numeric",
  hour: "2-digit",
  minute: "2-digit",
  hourCycle: "h23",
  timeZone: "UTC",
  timeZoneName: "short",
});
const countFormat = new Intl.NumberFormat(LOCALE);

const missing = (): string => t("format.missing");

function isUsable(value: number | null | undefined): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

/** '5 min', '2 h 10 min', '3 d 4 h', 'less than 1 min'. The smaller unit is dropped when it is zero. */
export function formatDuration(seconds: number | null | undefined): string {
  if (!isUsable(seconds) || seconds < 0) return missing();
  const total = Math.floor(seconds);
  if (total < MINUTE) return t("format.lessThanMinute");
  const days = Math.floor(total / DAY);
  const hours = Math.floor((total % DAY) / HOUR);
  const minutes = Math.floor((total % HOUR) / MINUTE);
  if (days > 0) return hours > 0 ? t("format.daysHours", { days, hours }) : t("format.days", { days });
  if (hours > 0) return minutes > 0 ? t("format.hoursMinutes", { hours, minutes }) : t("format.hours", { hours });
  return t("format.minutes", { minutes });
}

/** A moment from an ISO string, always read in UTC with the zone named. */
export function formatMoment(iso: string | null | undefined): string {
  if (!iso) return missing();
  const moment = new Date(iso);
  return Number.isNaN(moment.getTime()) ? missing() : momentFormat.format(moment);
}

export function formatCount(value: number | null | undefined): string {
  return isUsable(value) ? countFormat.format(value) : missing();
}
