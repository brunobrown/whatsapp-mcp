import { useRef, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useT } from "@/i18n/t";

/**
 * What the form holds for a secret: the marker the server sent (`{set}`, meaning untouched), the new text
 * to store, or `{clear: true}`. The stored value itself never reaches the page.
 */
export type SecretDraft = { set: boolean } | string | { clear: true };

export function isSecretClear(value: unknown): value is { clear: true } {
  return typeof value === "object" && value !== null && "clear" in value && value.clear === true;
}

/** The marker of an untouched secret, read from what the server sent. */
export function secretMarker(loaded: unknown): { set: boolean } {
  const set = typeof loaded === "object" && loaded !== null && "set" in loaded && loaded.set === true;
  return { set };
}

export interface SecretFieldProps {
  label: string;
  /** The draft; anything else than the three shapes counts as untouched. */
  value: unknown;
  /** The value as the server sent it: only its set/not set marker is read. */
  loaded: unknown;
  id: string;
  describedBy?: string | undefined;
  invalid?: boolean;
  disabled?: boolean;
  onChange: (value: SecretDraft) => void;
}

/** A secret shows only whether it is set. The operator can replace it, clear it, or leave it alone. */
export function SecretField({ label, value, loaded, id, describedBy, invalid = false, disabled = false, onChange }: SecretFieldProps) {
  const t = useT();
  const untouched = secretMarker(loaded);
  const input = useRef<HTMLInputElement>(null);
  // The input stays open while the operator is typing, also after they empty it. A parent that discards
  // the draft remounts the field (a new key) to close it.
  const [opened, setOpened] = useState(false);
  const replacing = typeof value === "string" || opened;
  const clearing = isSecretClear(value);

  const status = clearing
    ? t("secret.willClear")
    : replacing
      ? t("secret.willReplace")
      : untouched.set
        ? t("secret.set")
        : t("secret.notSet");

  const startReplace = (): void => {
    setOpened(true);
    // The input mounts on the next render; the focus follows it there.
    queueMicrotask(() => input.current?.focus());
  };
  const back = (): void => {
    setOpened(false);
    onChange(untouched);
  };

  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center gap-2">
        <Badge variant={clearing ? "warning" : replacing || untouched.set ? "accent" : "neutral"}>{status}</Badge>
        {replacing || clearing ? null : (
          <>
            <Button variant="secondary" disabled={disabled} onClick={startReplace}>
              {untouched.set ? t("secret.replace") : t("secret.setValue")}
            </Button>
            {untouched.set ? (
              <Button
                variant="secondary"
                disabled={disabled}
                onClick={() => {
                  onChange({ clear: true });
                }}
              >
                {t("secret.clear")}
              </Button>
            ) : null}
          </>
        )}
        {clearing ? (
          <Button variant="ghost" disabled={disabled} onClick={back}>
            {t("secret.undo")}
          </Button>
        ) : null}
      </div>
      {replacing ? (
        <div className="flex flex-col gap-2 sm:flex-row">
          <Input
            ref={input}
            id={id}
            type="password"
            name={`secret-${id}`}
            autoComplete="new-password"
            spellCheck={false}
            aria-label={t("secret.newValue", { label })}
            aria-describedby={describedBy}
            aria-invalid={invalid || undefined}
            disabled={disabled}
            value={typeof value === "string" ? value : ""}
            onChange={(event) => {
              const text = event.target.value;
              onChange(text === "" ? untouched : text);
            }}
          />
          <Button variant="ghost" disabled={disabled} onClick={back}>
            {t("secret.cancel")}
          </Button>
        </div>
      ) : null}
    </div>
  );
}
