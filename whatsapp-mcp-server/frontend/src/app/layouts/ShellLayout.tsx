import {
  Activity,
  ArrowLeft,
  Clock,
  Database,
  Eye,
  EyeOff,
  History,
  Image as ImageIcon,
  Lock,
  LogOut,
  Monitor,
  Moon,
  Radio,
  Settings,
  Shield,
  Sun,
  User,
  Webhook,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { Link, Outlet, useNavigate, useRouterState } from "@tanstack/react-router";
import { useEffect, useRef, useState } from "react";
import { ErrorState, Skeleton } from "@/components/feedback/feedback";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/dialog";
import { useSessionWatch } from "@/features/auth/useSessionWatch";
import { useT } from "@/i18n/t";
import { cn } from "@/lib/utils";
import { useMediaQuery, WIDE_QUERY } from "@/lib/useMediaQuery";
import { sessionService, useSections } from "@/services/services";
import { useUiStore } from "@/stores/ui";
import type { Theme } from "@/stores/ui";
import type { SectionSummary } from "@/types/api";

/** The icons the sections can name. An unknown name falls back to a gear. */
const ICONS: Record<string, LucideIcon> = {
  activity: Activity,
  clock: Clock,
  database: Database,
  eye: Eye,
  "eye-off": EyeOff,
  history: History,
  image: ImageIcon,
  lock: Lock,
  monitor: Monitor,
  radio: Radio,
  shield: Shield,
  user: User,
  webhook: Webhook,
};

const THEME_ORDER: readonly Theme[] = ["system", "light", "dark"];
const THEME_ICONS: Record<Theme, LucideIcon> = { system: Monitor, light: Sun, dark: Moon };

/** The path of an entry: a section lives under /s, a page at its own id. */
function pathOf(entry: SectionSummary): string {
  return entry.kind === "section" ? `/s/${entry.id}` : `/${entry.id}`;
}

function SectionLink({ entry, pending, onOpen }: { entry: SectionSummary; pending: boolean; onOpen: () => void }) {
  const t = useT();
  const Icon = ICONS[entry.icon] ?? Settings;
  const className =
    "flex min-h-16 items-center gap-3 px-4 py-2.5 text-ink no-underline hover:bg-hover aria-[current=page]:bg-selected";
  const inner = (
    <>
      <span className="flex size-10 shrink-0 items-center justify-center rounded-full bg-accent-soft text-accent-ink">
        <Icon aria-hidden="true" className="size-5" />
      </span>
      <span className="min-w-0 flex-1">
        <span className="block truncate font-medium">{entry.title}</span>
        {entry.description ? <span className="block truncate text-sm text-muted">{entry.description}</span> : null}
      </span>
      {pending ? <Badge variant="warning">{t("shell.pendingRestart")}</Badge> : null}
    </>
  );
  return entry.kind === "section" ? (
    <Link to="/s/$sectionId" params={{ sectionId: entry.id }} className={className} onClick={onOpen}>
      {inner}
    </Link>
  ) : (
    <Link to={pathOf(entry)} className={className} onClick={onOpen}>
      {inner}
    </Link>
  );
}

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

function ListPane({ onOpen }: { onOpen: () => void }) {
  const t = useT();
  const sections = useSections();
  const restartSections = useUiStore((state) => state.restartSections);
  return (
    <aside aria-label={t("shell.panel")} className="flex min-h-0 min-w-0 flex-1 flex-col bg-panel">
      <div className="flex h-16 shrink-0 items-center gap-1 border-b border-line bg-surface pr-2 pl-4">
        <div className="min-w-0 flex-1 leading-tight">
          <p className="m-0 truncate text-lg font-semibold">{t("app.name")}</p>
          <p className="m-0 truncate text-sm text-muted">{t("app.settings")}</p>
        </div>
        <ThemeButton />
        <SignOutButton />
      </div>
      <nav aria-label={t("shell.nav")} className="min-h-0 flex-1 overflow-y-auto" aria-busy={sections.isPending}>
        {sections.isPending ? (
          <div className="flex flex-col gap-3 p-4">
            <Skeleton className="h-12" />
            <Skeleton className="h-12" />
            <Skeleton className="h-12" />
          </div>
        ) : sections.isError ? (
          <div className="px-4">
            <ErrorState
              title={t("shell.loadError")}
              onRetry={() => {
                void sections.refetch();
              }}
            />
          </div>
        ) : (
          <ul className="m-0 flex list-none flex-col p-0">
            {sections.data.map((entry) => (
              <li key={entry.id} className="border-b border-line">
                <SectionLink
                  entry={entry}
                  pending={entry.pending || restartSections.includes(entry.id)}
                  onOpen={onOpen}
                />
              </li>
            ))}
          </ul>
        )}
      </nav>
    </aside>
  );
}

function DetailPane({ active, showBack, onBack }: { active: SectionSummary | undefined; showBack: boolean; onBack: () => void }) {
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
 * Two panes from 600 px: the section list on the left like a chat list, the open section on the right under
 * its own header. Below 600 px one pane at a time, with a back button on the detail.
 */
export function ShellLayout() {
  const t = useT();
  useSessionWatch();
  const wide = useMediaQuery(WIDE_QUERY);
  const pane = useUiStore((state) => state.mobilePane);
  const setPane = useUiStore((state) => state.setMobilePane);
  const pathname = useRouterState({ select: (state) => state.location.pathname });
  const sections = useSections();

  const active = sections.data?.find((entry) => pathOf(entry) === pathname);

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
        <div className={cn("flex min-w-0 flex-col", wide ? "w-[30%] max-w-[420px] min-w-72 shrink-0 border-r border-line" : "flex-1")}>
          <ListPane
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
