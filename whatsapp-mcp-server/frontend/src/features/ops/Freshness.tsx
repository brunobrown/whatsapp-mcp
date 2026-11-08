import { RefreshCw, TriangleAlert } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useT } from "@/i18n/t";
import { useFreshness } from "@/lib/useFreshness";
import type { FreshnessSource } from "@/lib/useFreshness";

/**
 * 'Updated N s ago', a 'Could not update' note when the last fetch failed (the data on the page is then the previous
 * one) and a Refresh button, for the queries a live page shows.
 */
export function Freshness({ queries }: { queries: readonly FreshnessSource[] }) {
  const t = useT();
  const freshness = useFreshness(queries);
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-sm text-muted">
      {freshness.label ? <span data-freshness>{freshness.label}</span> : null}
      <span role="status" className="inline-flex items-center gap-1 text-warning-ink">
        {freshness.failed ? (
          <>
            <TriangleAlert aria-hidden="true" className="size-4" />
            {t("freshness.failed")}
          </>
        ) : null}
      </span>
      <Button variant="secondary" busy={freshness.refreshing} onClick={freshness.refresh} className="ml-auto">
        <RefreshCw aria-hidden="true" className={freshness.refreshing ? "animate-spin" : undefined} />
        {t("freshness.refresh")}
      </Button>
    </div>
  );
}
