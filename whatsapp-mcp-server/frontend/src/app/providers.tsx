import { QueryClientProvider } from "@tanstack/react-query";
import { useEffect } from "react";
import type { ReactNode } from "react";
import { Toaster } from "@/components/feedback/toaster";
import { onUnauthorized } from "@/lib/api";
import { queryClient } from "@/lib/queryClient";
import type { AppRouter } from "./router";

/** The query client, the toasts, and the one place that sends a lost session back to sign-in. */
export function AppProviders({ router, children }: { router: AppRouter; children: ReactNode }) {
  useEffect(
    () =>
      onUnauthorized(() => {
        void router.navigate({ to: "/login", replace: true });
      }),
    [router],
  );
  return (
    <QueryClientProvider client={queryClient}>
      {children}
      <Toaster />
    </QueryClientProvider>
  );
}
