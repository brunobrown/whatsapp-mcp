import { AuthLayout } from "@/app/layouts/AuthLayout";
import { Banner, ErrorState, Skeleton } from "@/components/feedback/feedback";
import { useT } from "@/i18n/t";
import { useSession } from "@/services/services";
import { LoginPage } from "./LoginPage";

/** The page of /login: it reads the session and shows the form that state calls for. */
export function AuthPage() {
  const t = useT();
  const session = useSession();

  if (session.isPending) {
    return (
      <AuthLayout title={t("auth.login.title")}>
        <div aria-busy="true" aria-label={t("auth.loading")} className="flex flex-col gap-3">
          <Skeleton className="h-11" />
          <Skeleton className="h-11" />
        </div>
      </AuthLayout>
    );
  }
  if (session.isError) {
    return (
      <AuthLayout title={t("auth.login.title")}>
        <ErrorState
          title={session.error.message}
          onRetry={() => {
            void session.refetch();
          }}
        />
      </AuthLayout>
    );
  }
  if (session.data.state === "broken") {
    return (
      <AuthLayout title={t("auth.broken.title")}>
        <Banner kind="error">{session.data.message ?? t("error.unexpected")}</Banner>
      </AuthLayout>
    );
  }
  return <LoginPage notice={session.data.message} />;
}
