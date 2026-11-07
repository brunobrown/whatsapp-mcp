import {
  createRootRoute,
  createRoute,
  createRouter,
  Link,
  Outlet,
  redirect,
} from "@tanstack/react-router";
import type { RouterHistory } from "@tanstack/react-router";
import { ShellLayout } from "@/app/layouts/ShellLayout";
import { AuthPage } from "@/features/auth/AuthPage";
import { ErrorState } from "@/components/feedback/feedback";
import { buttonVariants } from "@/components/ui/button";
import { useT } from "@/i18n/t";
import { queryClient, SESSION_KEY } from "@/lib/queryClient";
import { keys, sectionsService, sessionService } from "@/services/services";
import type { SectionSummary, SessionInfo } from "@/types/api";

/** The session as the cache holds it, read from the server when the cache has none. */
async function currentSession(): Promise<SessionInfo> {
  return queryClient.query({ queryKey: SESSION_KEY, queryFn: () => sessionService.get(), staleTime: "static" });
}

async function requireSession(): Promise<void> {
  const session = await currentSession();
  if (session.state !== "authenticated") throw redirect({ to: "/login" });
}

/** The first section to open: lowest `order` among sections; a page only when there is no section. */
export function firstSection(list: readonly SectionSummary[]): SectionSummary | undefined {
  const byOrder = [...list].sort((a, b) => a.order - b.order);
  return byOrder.find((item) => item.kind === "section") ?? byOrder[0];
}

function NotFoundPage() {
  const t = useT();
  return (
    <main className="mx-auto flex min-h-dvh max-w-md flex-col items-start justify-center gap-3 p-6">
      <h1 className="m-0 text-2xl font-semibold">{t("notFound.title")}</h1>
      <p className="m-0 text-muted">{t("notFound.body")}</p>
      <Link to="/" className={buttonVariants({ variant: "primary" })}>
        {t("notFound.link")}
      </Link>
    </main>
  );
}

function RouteError({ reset }: { reset: () => void }) {
  const t = useT();
  return (
    <main className="mx-auto max-w-md p-6">
      <ErrorState title={t("route.error")} onRetry={reset} />
    </main>
  );
}

const rootRoute = createRootRoute({
  component: Outlet,
  notFoundComponent: NotFoundPage,
  errorComponent: RouteError,
});

const loginRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/login",
  component: AuthPage,
  beforeLoad: async () => {
    const session = await currentSession();
    if (session.state === "authenticated") throw redirect({ to: "/" });
  },
});

const indexRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/",
  beforeLoad: async () => {
    await requireSession();
    const list = await queryClient.query({ queryKey: keys.sections, queryFn: () => sectionsService.list(), staleTime: 0 });
    const first = firstSection(list);
    if (first === undefined) throw redirect({ to: "/status" });
    throw redirect({ to: "/s/$sectionId", params: { sectionId: first.id } });
  },
});

/** Everything behind the sign-in lives inside the two-pane shell. */
const shellRoute = createRoute({
  getParentRoute: () => rootRoute,
  id: "shell",
  beforeLoad: requireSession,
  component: ShellLayout,
});

const sectionRoute = createRoute({ getParentRoute: () => shellRoute, path: "/s/$sectionId" });
const statusRoute = createRoute({ getParentRoute: () => shellRoute, path: "/status" });
const activityRoute = createRoute({ getParentRoute: () => shellRoute, path: "/activity" });
const accountRoute = createRoute({ getParentRoute: () => shellRoute, path: "/account" });

const routeTree = rootRoute.addChildren([
  loginRoute,
  indexRoute,
  shellRoute.addChildren([sectionRoute, statusRoute, activityRoute, accountRoute]),
]);

export function createAppRouter(history?: RouterHistory) {
  return createRouter({ routeTree, ...(history && { history }), defaultPreload: "intent" });
}

export type AppRouter = ReturnType<typeof createAppRouter>;

declare module "@tanstack/react-router" {
  interface Register {
    router: AppRouter;
  }
}
