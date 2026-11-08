import { ErrorState, Skeleton } from "@/components/feedback/feedback";
import { useT } from "@/i18n/t";
import { useSections } from "@/services/services";
import { useUiStore } from "@/stores/ui";
import { SideMenu } from "./SideMenu";
import type { SideMenuMode } from "./SideMenu";

/** The section menu with its loading and failure states; the shell and the drawer both show it. */
export function MenuBody({ mode, onNavigate }: { mode: SideMenuMode; onNavigate?: () => void }) {
  const t = useT();
  const sections = useSections();
  const restartSections = useUiStore((state) => state.restartSections);
  if (sections.isPending) {
    return (
      <div role="status" aria-label={t("shell.nav")} aria-busy="true" className="min-h-0 flex-1">
        <div className="flex flex-col gap-3 p-4">
          <Skeleton className="h-12" />
          <Skeleton className="h-12" />
          <Skeleton className="h-12" />
        </div>
      </div>
    );
  }
  if (sections.isError) {
    return (
      <div className="min-h-0 flex-1 overflow-y-auto px-4">
        <ErrorState
          title={t("shell.loadError")}
          onRetry={() => {
            void sections.refetch();
          }}
        />
      </div>
    );
  }
  return (
    <SideMenu
      entries={sections.data}
      mode={mode}
      isPending={(entry) => entry.pending || restartSections.includes(entry.id)}
      {...(onNavigate && { onNavigate })}
    />
  );
}
