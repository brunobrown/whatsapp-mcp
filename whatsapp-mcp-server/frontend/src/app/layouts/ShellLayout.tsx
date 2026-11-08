import { PanelLeftClose, PanelLeftOpen } from "lucide-react";
import { Outlet, useRouterState } from "@tanstack/react-router";
import { useLayoutEffect, useMemo, useRef, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { useSessionWatch } from "@/features/auth/useSessionWatch";
import { MenuBody } from "@/features/menu/MenuBody";
import { MenuDrawer } from "@/features/menu/MenuDrawer";
import { MenuTools } from "@/features/menu/MenuTools";
import { pathOf } from "@/features/menu/model";
import { SectionSlotsContext } from "@/features/sections/slots";
import { useT } from "@/i18n/t";
import { cn } from "@/lib/utils";
import { DESKTOP_QUERY, useMediaQuery, WIDE_QUERY } from "@/lib/useMediaQuery";
import { useSections } from "@/services/services";
import { MENU_BESIDE_FROM, MENU_EXPANDED_FROM, resolveMenu, useUiStore } from "@/stores/ui";
import type { SectionSummary } from "@/types/api";

function ListPane({ rail }: { rail: boolean }) {
  const t = useT();
  const setMenu = useUiStore((state) => state.setMenu);
  return (
    <aside
      aria-label={t("shell.panel")}
      className={cn(
        "flex min-h-0 min-w-0 shrink-0 flex-col border-r border-line bg-panel",
        rail ? "w-[72px]" : "w-[320px]",
      )}
    >
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
      </div>
      <MenuBody mode={rail ? "rail" : "expanded"} />
      {rail ? (
        <div className="flex shrink-0 flex-col items-center gap-1 border-t border-line bg-surface py-2">
          <MenuTools />
        </div>
      ) : null}
    </aside>
  );
}

function DetailPane({ active, drawer, pathname }: { active: SectionSummary | undefined; drawer: boolean; pathname: string }) {
  const t = useT();
  const content = useRef<HTMLElement>(null);
  // A new section opens at its top; the menu is another scroller and keeps its own position.
  useLayoutEffect(() => {
    if (content.current) content.current.scrollTop = 0;
  }, [pathname]);
  const [actions, setActions] = useState<HTMLElement | null>(null);
  const [bar, setBar] = useState<HTMLElement | null>(null);
  const slots = useMemo(() => ({ actions, bar }), [actions, bar]);
  const restartSections = useUiStore((state) => state.restartSections);
  const pending = active !== undefined && (active.pending || restartSections.includes(active.id));
  return (
    <SectionSlotsContext.Provider value={slots}>
    <div className="flex min-h-0 min-w-0 flex-1 flex-col">
      <header className="flex h-16 shrink-0 items-center gap-2 border-b border-line bg-surface px-2 sm:px-4">
        {drawer ? <MenuDrawer /> : null}
        <div className="min-w-0 flex-1 leading-tight">
          <h1 className="m-0 truncate text-lg font-semibold" title={active?.title}>
            {active?.title ?? t("app.settings")}
          </h1>
          {active?.description ? (
            <p className="m-0 truncate text-sm text-muted" title={active.description}>
              {active.description}
            </p>
          ) : null}
        </div>
        {pending ? <Badge variant="warning">{t("shell.pendingRestart")}</Badge> : null}
        <div ref={setActions} className="flex shrink-0 items-center empty:hidden" />
      </header>
      <main
        id="content"
        ref={content}
        tabIndex={-1}
        className="min-h-0 min-w-0 flex-1 overflow-x-hidden overflow-y-auto overscroll-contain"
      >
        <div className="mx-auto w-full max-w-3xl px-4 py-5 sm:px-6 sm:py-6">
          <Outlet />
        </div>
      </main>
      <div ref={setBar} className="shrink-0 empty:hidden" />
    </div>
    </SectionSlotsContext.Provider>
  );
}

/**
 * The menu on the left and the open section on the right under its own header. From 600 px the menu is 320 px wide
 * or a 72 px rail; below 600 px the section fills the screen and the menu is a drawer opened from its header.
 */
export function ShellLayout() {
  const t = useT();
  useSessionWatch();
  const wide = useMediaQuery(WIDE_QUERY);
  const desktop = useMediaQuery(DESKTOP_QUERY);
  const menu = useUiStore((state) => state.menu);
  const pathname = useRouterState({
    select: (state) => state.location.pathname,
  });
  const sections = useSections();

  const active = sections.data?.find((entry) => pathOf(entry) === pathname);
  const mode = resolveMenu(desktop ? MENU_EXPANDED_FROM : wide ? MENU_BESIDE_FROM : 0, menu);

  return (
    <div data-testid="shell" data-menu={mode} className="flex h-dvh w-full overflow-hidden bg-surface">
      <a
        href="#content"
        className="sr-only focus:not-sr-only focus:absolute focus:top-2 focus:left-2 focus:z-50 focus:rounded-control focus:bg-panel focus:px-3 focus:py-2"
      >
        {t("shell.skip")}
      </a>
      {mode === "drawer" ? null : <ListPane rail={mode === "collapsed"} />}
      <div className="flex min-w-0 flex-1 flex-col">
        <DetailPane active={active} drawer={mode === "drawer"} pathname={pathname} />
      </div>
    </div>
  );
}
