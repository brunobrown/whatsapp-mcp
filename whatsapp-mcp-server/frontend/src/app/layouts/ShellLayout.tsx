import { ArrowLeft, LogOut, Monitor, Moon, PanelLeftClose, PanelLeftOpen, Sun } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { Outlet, useNavigate, useRouterState } from "@tanstack/react-router";
import { useEffect, useRef, useState } from "react";
import { ErrorState, Skeleton } from "@/components/feedback/feedback";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/dialog";
import { useSessionWatch } from "@/features/auth/useSessionWatch";
import { pathOf } from "@/features/menu/model";
import { SideMenu } from "@/features/menu/SideMenu";
import { useT } from "@/i18n/t";
import { cn } from "@/lib/utils";
import { DESKTOP_QUERY, useMediaQuery, WIDE_QUERY } from "@/lib/useMediaQuery";
import { sessionService, useSections } from "@/services/services";
import { MENU_BESIDE_FROM, MENU_EXPANDED_FROM, resolveMenu, useUiStore } from "@/stores/ui";
import type { Theme } from "@/stores/ui";
import type { SectionSummary } from "@/types/api";

const THEME_ORDER: readonly Theme[] = ["system", "light", "dark"];
const THEME_ICONS: Record<Theme, LucideIcon> = {
  system: Monitor,
  light: Sun,
  dark: Moon,
};

function ThemeButton() {
  const t = useT();
  const theme = useUiStore((state) => state.theme);
  const setTheme = useUiStore((state) => state.setTheme);
  const Icon = THEME_ICONS[theme];
  const next = THEME_ORDER[(THEME_ORDER.indexOf(theme) + 1) % THEME_ORDER.length] ?? "system";
  return (
    <Button
      variant="ghost"
      size="icon"
      aria-label={t("shell.theme", { name: t(`shell.theme.${theme}`) })}
      onClick={() => {
        setTheme(next);
      }}
    >
      <Icon aria-hidden="true" />
    </Button>
  );
}

function SignOutButton() {
  const t = useT();
  const navigate = useNavigate();
  const [asking, setAsking] = useState(false);
  const signOut = () => {
    void sessionService
      .logout()
      .catch(() => undefined)
      .then(() => navigate({ to: "/login", replace: true }));
  };
  return (
    <>
      <Button
        variant="ghost"
        size="icon"
        aria-label={t("shell.signOut")}
        onClick={() => {
          if (useUiStore.getState().draftDirty) setAsking(true);
          else signOut();
        }}
      >
        <LogOut aria-hidden="true" />
      </Button>
      <ConfirmDialog
        open={asking}
        onOpenChange={setAsking}
        title={t("guard.signOut.title")}
        description={t("guard.signOut.description")}
        destructive
        consequence={t("guard.consequence")}
        confirmLabel={t("guard.signOut.confirm")}
        cancelLabel={t("guard.stay")}
        onConfirm={signOut}
      />
    </>
  );
}

function MenuTools() {
  return (
    <>
      <ThemeButton />
      <SignOutButton />
    </>
  );
}

function ListPane({ rail, collapsible, onOpen }: { rail: boolean; collapsible: boolean; onOpen: () => void }) {
  const t = useT();
  const sections = useSections();
  const restartSections = useUiStore((state) => state.restartSections);
  const setMenu = useUiStore((state) => state.setMenu);
  const collapse = collapsible ? (
    <Button
      variant="ghost"
      size="icon"
      aria-label={rail ? t("menu.expand") : t("menu.collapse")}
      aria-expanded={!rail}
      onClick={() => {
        setMenu(rail ? "expanded" : "collapsed");
      }}
    >
      {rail ? <PanelLeftOpen aria-hidden="true" /> : <PanelLeftClose aria-hidden="true" />}
    </Button>
  ) : null;
  return (
    <aside aria-label={t("shell.panel")} className="flex min-h-0 min-w-0 flex-1 flex-col bg-panel">
      <div
        className={cn(
          "flex h-16 shrink-0 items-center gap-1 border-b border-line bg-surface",
          rail ? "justify-center" : "pr-2 pl-4",
        )}
      >
        {rail ? null : (
          <div className="min-w-0 flex-1 leading-tight">
            <p className="m-0 truncate text-lg font-semibold">{t("app.name")}</p>
            <p className="m-0 truncate text-sm text-muted">{t("app.settings")}</p>
          </div>
        )}
        {rail ? null : <MenuTools />}
        {collapse}
      </div>
      {sections.isPending ? (
        <div role="status" aria-label={t("common.loading")} aria-busy="true" className="min-h-0 flex-1">
          <div className="flex flex-col gap-3 p-4">
            <Skeleton className="h-12" />
            <Skeleton className="h-12" />
            <Skeleton className="h-12" />
          </div>
        </div>
      ) : sections.isError ? (
        <div className="min-h-0 flex-1 overflow-y-auto">
          <div className="px-4">
            <ErrorState
              title={t("shell.loadError")}
              onRetry={() => {
                void sections.refetch();
              }}
            />
          </div>
        </div>
      ) : (
        <SideMenu
          entries={sections.data}
          mode={rail ? "rail" : "expanded"}
          isPending={(entry) => entry.pending || restartSections.includes(entry.id)}
          onNavigate={onOpen}
        />
      )}
      {rail ? (
        <div className="flex shrink-0 flex-col items-center gap-1 border-t border-line bg-surface py-2">
          <MenuTools />
        </div>
      ) : null}
    </aside>
  );
}

function DetailPane({
  active,
  showBack,
  onBack,
}: {
  active: SectionSummary | undefined;
  showBack: boolean;
  onBack: () => void;
}) {
  const t = useT();
  const restartSections = useUiStore((state) => state.restartSections);
  const pending = active !== undefined && (active.pending || restartSections.includes(active.id));
  return (
    <div className="flex min-h-0 min-w-0 flex-1 flex-col">
      <header className="flex h-16 shrink-0 items-center gap-2 border-b border-line bg-surface px-2 sm:px-4">
        {showBack ? (
          <Button variant="ghost" size="icon" aria-label={t("shell.back")} onClick={onBack}>
            <ArrowLeft aria-hidden="true" />
          </Button>
        ) : null}
        <h1 className="m-0 min-w-0 flex-1 truncate text-lg font-semibold">{active?.title ?? t("app.settings")}</h1>
        {pending ? <Badge variant="warning">{t("shell.pendingRestart")}</Badge> : null}
      </header>
      <main id="content" tabIndex={-1} className="min-h-0 min-w-0 flex-1 overflow-x-hidden overflow-y-auto">
        <div className="mx-auto w-full max-w-3xl px-4 py-5 sm:px-6 sm:py-6">
          <Outlet />
        </div>
      </main>
    </div>
  );
}

/**
 * The menu on the left and the open section on the right under its own header. The menu is 320 px wide or a 72 px
 * rail from 600 px; below 600 px one pane at a time, with a back button on the detail.
 */
export function ShellLayout() {
  const t = useT();
  useSessionWatch();
  const wide = useMediaQuery(WIDE_QUERY);
  const desktop = useMediaQuery(DESKTOP_QUERY);
  const menu = useUiStore((state) => state.menu);
  const pane = useUiStore((state) => state.mobilePane);
  const setPane = useUiStore((state) => state.setMobilePane);
  const pathname = useRouterState({
    select: (state) => state.location.pathname,
  });
  const sections = useSections();

  const active = sections.data?.find((entry) => pathOf(entry) === pathname);
  const mode = resolveMenu(desktop ? MENU_EXPANDED_FROM : wide ? MENU_BESIDE_FROM : 0, menu);

  // Choosing something while the screen is narrow moves to the detail; a first load keeps the list.
  const previous = useRef(pathname);
  useEffect(() => {
    if (previous.current !== pathname) setPane("detail");
    previous.current = pathname;
  }, [pathname, setPane]);

  const showList = wide || pane === "list";
  const showDetail = wide || pane === "detail";

  return (
    <div data-testid="shell" className="flex h-dvh w-full overflow-hidden bg-surface">
      <a
        href="#content"
        className="sr-only focus:not-sr-only focus:absolute focus:top-2 focus:left-2 focus:z-50 focus:rounded-control focus:bg-panel focus:px-3 focus:py-2"
      >
        {t("shell.skip")}
      </a>
      {showList ? (
        <div
          data-menu={mode}
          className={cn(
            "flex min-w-0 flex-col",
            wide ? "shrink-0 border-r border-line" : "flex-1",
            wide && (mode === "expanded" ? "w-[320px]" : "w-[72px]"),
          )}
        >
          <ListPane
            rail={wide && mode === "collapsed"}
            collapsible={wide}
            onOpen={() => {
              setPane("detail");
            }}
          />
        </div>
      ) : null}
      {showDetail ? (
        <div className="flex min-w-0 flex-1 flex-col">
          <DetailPane
            active={active}
            showBack={!wide}
            onBack={() => {
              setPane("list");
            }}
          />
        </div>
      ) : null}
    </div>
  );
}
