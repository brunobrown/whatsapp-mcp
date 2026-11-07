import { useNavigate } from "@tanstack/react-router";
import { useRef, useState } from "react";
import type { SyntheticEvent } from "react";
import { AuthLayout } from "@/app/layouts/AuthLayout";
import { Banner } from "@/components/feedback/feedback";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useT } from "@/i18n/t";
import { isApiError, normalizeError } from "@/lib/api";
import { sessionService } from "@/services/services";
import { useCountdown } from "./useCountdown";

/** The server prints a code at most this often; the page shows the same interval. */
export const CODE_INTERVAL_SECONDS = 30;

export function SetupPage() {
  const t = useT();
  const navigate = useNavigate();
  const passwordField = useRef<HTMLInputElement>(null);
  const [code, setCode] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [codeMessage, setCodeMessage] = useState<string | null>(null);
  const [asking, setAsking] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [codeWait, startCodeWait] = useCountdown();
  const [wait, startWait] = useCountdown();

  const requestCode = async (): Promise<void> => {
    setAsking(true);
    try {
      const result = await sessionService.requestCode();
      // The code goes to the bridge log only. The answer is read for its message, never for a code.
      setCodeMessage(result.message ?? null);
      if (result.announced) startCodeWait(CODE_INTERVAL_SECONDS);
    } catch (error) {
      setCodeMessage(normalizeError(error).message);
    }
    setAsking(false);
  };

  const submit = async (event: SyntheticEvent): Promise<void> => {
    event.preventDefault();
    if (busy || wait > 0) return;
    setBusy(true);
    setFailure(null);
    try {
      const result = await sessionService.setup({ code, password, confirm });
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
    setConfirm("");
    setBusy(false);
    passwordField.current?.focus();
  };

  return (
    <AuthLayout title={t("auth.setup.title")} intro={t("auth.setup.intro")}>
      <div className="flex flex-col gap-2">
        <Button variant="secondary" busy={asking} disabled={codeWait > 0} onClick={() => void requestCode()}>
          {codeWait > 0 ? t("auth.setup.requestWait", { seconds: codeWait }) : t("auth.setup.requestCode")}
        </Button>
        <p className="m-0 text-sm text-muted">{t("auth.setup.interval", { seconds: CODE_INTERVAL_SECONDS })}</p>
        <div role="status">{codeMessage ? <Banner kind="info">{codeMessage}</Banner> : null}</div>
      </div>

      <form
        onSubmit={(event) => {
          void submit(event);
        }}
        className="mt-6 flex flex-col gap-4 border-t border-line pt-6"
        noValidate
      >
        {failure ? <Banner kind="error">{failure}</Banner> : null}
        <div className="flex flex-col gap-1.5">
          <label htmlFor="setup-code" className="font-medium">
            {t("auth.setup.code")}
          </label>
          <Input
            id="setup-code"
            name="code"
            autoComplete="off"
            autoCapitalize="characters"
            spellCheck={false}
            aria-describedby="setup-code-help"
            value={code}
            onChange={(event) => {
              setCode(event.target.value);
            }}
          />
          <p id="setup-code-help" className="m-0 text-sm text-muted">
            {t("auth.setup.codeHelp")}
          </p>
        </div>
        <div className="flex flex-col gap-1.5">
          <label htmlFor="setup-password" className="font-medium">
            {t("auth.setup.password")}
          </label>
          <Input
            ref={passwordField}
            id="setup-password"
            name="password"
            type="password"
            autoComplete="new-password"
            aria-describedby="setup-password-help"
            value={password}
            onChange={(event) => {
              setPassword(event.target.value);
            }}
          />
          <p id="setup-password-help" className="m-0 text-sm text-muted">
            {t("auth.setup.passwordHelp")}
          </p>
        </div>
        <div className="flex flex-col gap-1.5">
          <label htmlFor="setup-confirm" className="font-medium">
            {t("auth.setup.confirm")}
          </label>
          <Input
            id="setup-confirm"
            name="confirm"
            type="password"
            autoComplete="new-password"
            value={confirm}
            onChange={(event) => {
              setConfirm(event.target.value);
            }}
          />
        </div>
        <Button type="submit" busy={busy} disabled={wait > 0}>
          {wait > 0 ? t("auth.waitButton", { seconds: wait }) : t("auth.setup.submit")}
        </Button>
      </form>
    </AuthLayout>
  );
}
