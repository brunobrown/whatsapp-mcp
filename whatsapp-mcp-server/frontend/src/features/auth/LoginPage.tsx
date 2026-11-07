import { useNavigate } from "@tanstack/react-router";
import { useEffect, useRef, useState } from "react";
import type { SyntheticEvent } from "react";
import { AuthLayout } from "@/app/layouts/AuthLayout";
import { Banner } from "@/components/feedback/feedback";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useT } from "@/i18n/t";
import { isApiError, normalizeError } from "@/lib/api";
import { sessionService } from "@/services/services";

/** Counts a wait down by the second. Returns the seconds left and a way to start one. */
export function useCountdown(): [number, (seconds: number) => void] {
  const [until, setUntil] = useState(0);
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (until === 0) return;
    const timer = setInterval(() => {
      const current = Date.now();
      setNow(current);
      if (current >= until) clearInterval(timer);
    }, 1000);
    return () => {
      clearInterval(timer);
    };
  }, [until]);
  const start = (seconds: number): void => {
    const current = Date.now();
    setNow(current);
    setUntil(current + seconds * 1000);
  };
  return [Math.max(0, Math.ceil((until - now) / 1000)), start];
}

export function LoginPage({ notice }: { notice?: string | undefined }) {
  const t = useT();
  const navigate = useNavigate();
  const field = useRef<HTMLInputElement>(null);
  const [password, setPassword] = useState("");
  const [failure, setFailure] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [wait, startWait] = useCountdown();

  const submit = async (event: SyntheticEvent): Promise<void> => {
    event.preventDefault();
    if (busy || wait > 0 || password === "") return;
    setBusy(true);
    setFailure(null);
    try {
      const result = await sessionService.login({ password });
      if (result.ok) {
        await navigate({ to: "/", replace: true });
        return;
      }
      setFailure(result.message ?? t("error.unexpected"));
      if (result.wait) startWait(result.wait);
    } catch (error) {
      const refused = normalizeError(error);
      setFailure(refused.message);
      if (isApiError(refused) && refused.wait) startWait(refused.wait);
    }
    setPassword("");
    setBusy(false);
    field.current?.focus();
  };

  return (
    <AuthLayout title={t("auth.login.title")} intro={t("auth.login.intro")}>
      <form
        onSubmit={(event) => {
          void submit(event);
        }}
        className="flex flex-col gap-4"
        noValidate
      >
        {notice ? <Banner kind="info">{notice}</Banner> : null}
        {failure ? <Banner kind="error">{failure}</Banner> : null}
        <div className="flex flex-col gap-1.5">
          <label htmlFor="login-password" className="font-medium">
            {t("auth.password")}
          </label>
          <Input
            ref={field}
            id="login-password"
            name="password"
            type="password"
            autoComplete="current-password"
            value={password}
            aria-invalid={failure ? true : undefined}
            onChange={(event) => {
              setPassword(event.target.value);
            }}
          />
        </div>
        <Button type="submit" busy={busy} disabled={wait > 0}>
          {wait > 0 ? t("auth.waitButton", { seconds: wait }) : t("auth.login.submit")}
        </Button>
      </form>
    </AuthLayout>
  );
}
