import { RelativeTime } from "@/components/feedback/RelativeTime";
import { t } from "@/i18n/t";

/** A moment as relative text ('2 h ago') with the exact UTC time in `title`; a dash when there is none. */
export function Moment({ value }: { value: string | null | undefined }) {
  if (!value || !Number.isFinite(Date.parse(value))) return <>{t("format.missing")}</>;
  return <RelativeTime value={value} />;
}
