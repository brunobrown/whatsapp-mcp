import { exactUtc, relativeText, untilText } from "@/lib/relativeTime";
import { useNow } from "@/lib/useFreshness";

export interface RelativeTimeProps {
  /** An ISO moment or epoch milliseconds. */
  value: string | number | null | undefined;
  className?: string;
}

/** A moment as '2 h ago' (or 'in 2 h' while it is ahead), with the exact UTC time in `title` and the ISO moment in `datetime`. */
export function RelativeTime({ value, className }: RelativeTimeProps) {
  const now = useNow();
  const moment = typeof value === "number" ? value : value ? Date.parse(value) : Number.NaN;
  if (!Number.isFinite(moment)) return null;
  return (
    <time dateTime={new Date(moment).toISOString()} title={exactUtc(moment)} className={className}>
      {moment > now + 1000 ? untilText(now, moment) : relativeText(now, moment)}
    </time>
  );
}
