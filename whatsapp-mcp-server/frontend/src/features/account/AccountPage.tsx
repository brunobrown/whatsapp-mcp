import { useNavigate } from "@tanstack/react-router";
import { useRef, useState } from "react";
import type { Ref, SyntheticEvent } from "react";
import { Banner } from "@/components/feedback/feedback";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useCountdown } from "@/features/auth/useCountdown";
import { useT } from "@/i18n/t";
import { isApiError, normalizeError } from "@/lib/api";
import { sessionService } from "@/services/services";

function PasswordField({
  id,
  label,
  autoComplete,
  value,
  onChange,
  invalid,
  help,
  inputRef,
}: {
  id: string;
  label: string;
  autoComplete: "current-password" | "new-password";
  value: string;
  onChange: (value: string) => void;
  invalid: boolean;
  help?: string;
  inputRef?: Ref<HTMLInputElement>;
}) {
  return (
    <div className="flex flex-col gap-1.5">
      <label htmlFor={id} className="font-medium">
        {label}
      </label>
      <Input
        ref={inputRef}
        id={id}
        type="password"
        autoComplete={autoComplete}
        value={value}
        aria-invalid={invalid ? true : undefined}
        {...(help !== undefined && { "aria-describedby": `${id}-help` })}
        onChange={(event) => {
          onChange(event.target.value);
        }}
      />
      {help ? (
        <p id={`${id}-help`} className="m-0 text-sm text-muted">
          {help}
        </p>
      ) : null}
    </div>
  );
}

function ChangePassword() {
  const t = useT();
  const first = useRef<HTMLInputElement>(null);
  const [current, setCurrent] = useState("");
  const [fresh, setFresh] = useState("");
  const [confirm, setConfirm] = useState("");
  const [failure, setFailure] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [wait, startWait] = useCountdown();

  const submit = async (event: SyntheticEvent): Promise<void> => {
    event.preventDefault();
    if (busy || wait > 0) return;
    setBusy(true);
    setFailure(null);
    setDone(null);
    let ok = false;
    try {
      const result = await sessionService.changePassword({ current, new: fresh, confirm });
      ok = result.ok;
      if (result.ok) setDone(result.message ?? t("account.changed"));
      else {
        setFailure(result.message ?? t("error.unexpected"));
        if (result.wait) startWait(result.wait);
      }
    } catch (error) {
      const refused = normalizeError(error);
      setFailure(refused.message);
      if (isApiError(refused) && refused.wait) startWait(refused.wait);
    }
    // Passwords never stay in the form after an attempt.
    setCurrent("");
    setFresh("");
    setConfirm("");
    setBusy(false);
    if (!ok) first.current?.focus();
  };

  const invalid = failure !== null;
  return (
    <section className="flex flex-col gap-4">
      <div>
        <h2 className="m-0 text-base font-semibold">{t("account.password")}</h2>
        <p className="m-0 mt-1 text-muted">{t("account.password.intro")}</p>
      </div>
      <form
        onSubmit={(event) => {
          void submit(event);
        }}
        className="flex max-w-md flex-col gap-4"
        noValidate
      >
        {failure ? <Banner kind="error">{failure}</Banner> : null}
        {done ? <Banner kind="success">{done}</Banner> : null}
        <PasswordField
          id="account-current"
          label={t("account.current")}
          autoComplete="current-password"
          value={current}
          onChange={setCurrent}
          invalid={invalid}
          inputRef={first}
        />
        <PasswordField
          id="account-new"
          label={t("account.new")}
          autoComplete="new-password"
          value={fresh}
          onChange={setFresh}
          invalid={invalid}
          help={t("auth.setup.passwordHelp")}
        />
        <PasswordField
          id="account-confirm"
          label={t("account.confirm")}
          autoComplete="new-password"
          value={confirm}
          onChange={setConfirm}
          invalid={invalid}
        />
        <Button type="submit" busy={busy} disabled={wait > 0} className="self-start">
          {wait > 0 ? t("auth.waitButton", { seconds: wait }) : t("account.submit")}
        </Button>
      </form>
    </section>
  );
}

function SignOut() {
  const t = useT();
  const navigate = useNavigate();
  const [busy, setBusy] = useState(false);
  return (
    <section className="flex flex-col gap-3 border-t border-line pt-6">
      <div>
        <h2 className="m-0 text-base font-semibold">{t("account.session")}</h2>
        <p className="m-0 mt-1 text-muted">{t("account.session.intro")}</p>
      </div>
      <Button
        variant="secondary"
        busy={busy}
        className="self-start"
        onClick={() => {
          setBusy(true);
          // The cache is cleared whether or not the server answered, then the sign-in page takes over.
          void sessionService
            .logout()
            .catch(() => undefined)
            .then(() => navigate({ to: "/login", replace: true }));
        }}
      >
        {t("account.signOut")}
      </Button>
    </section>
  );
}

/** Change the password and sign out. */
export function AccountPage() {
  return (
    <div className="flex flex-col gap-8">
      <ChangePassword />
      <SignOut />
    </div>
  );
}
