import type { ReactNode } from "react";
import { useT } from "@/i18n/t";

/** The frame of the pages before sign-in: a green band with the project name and a card over its edge. */
export function AuthLayout({ title, intro, children }: { title: string; intro?: string; children: ReactNode }) {
  const t = useT();
  return (
    <div className="min-h-dvh w-full overflow-x-hidden bg-surface">
      <div className="h-52 bg-accent-solid px-6 pt-8 text-on-accent sm:h-56">
        <p className="m-0 mx-auto max-w-md text-sm font-medium tracking-wide uppercase">{t("app.name")}</p>
      </div>
      <main className="mx-auto -mt-28 w-full max-w-md px-4 pb-10 sm:-mt-32">
        <div className="rounded-card border border-line bg-panel p-6 shadow-sm sm:p-8">
          <h1 className="m-0 text-2xl font-semibold">{title}</h1>
          {intro ? <p className="mt-1.5 mb-0 text-muted">{intro}</p> : null}
          <div className="mt-6">{children}</div>
        </div>
      </main>
    </div>
  );
}
