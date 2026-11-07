import { CircleAlert, Lock, RotateCcw, TriangleAlert } from "lucide-react";
import { useId, useState } from "react";
import type { ReactNode } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { CheckboxGroup, RadioGroup, Select, Switch } from "@/components/ui/controls";
import { ConfirmDialog } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { SecretField } from "./SecretField";
import { useT } from "@/i18n/t";
import { cn } from "@/lib/utils";
import type { FieldSchema, FieldState } from "@/types/api";

/** The most options a choice shows as radio buttons; more become a select. */
export const MAX_RADIO_OPTIONS = 4;

/**
 * What a control holds while the operator edits: the raw text for text, int and float (the server reads it), a
 * boolean for bool, the option for choice and the chosen options for multichoice. Lists, secrets and records
 * have their own shapes, owned by their fields.
 */
export type EditorValue = unknown;

export interface FieldRendererProps {
  field: FieldSchema;
  value: EditorValue;
  /** The value as the server sent it; secrets read their set/not set marker from here. */
  loaded?: EditorValue;
  state?: FieldState | undefined;
  /** The message the server gave for this field. */
  error?: string | undefined;
  /** Makes ids unique when the same field appears on several cards. */
  idPrefix?: string;
  onChange: (value: EditorValue) => void;
  /** Called once the operator confirms the reset of this field to its default. */
  onRestore?: (key: string) => void;
}

const textClass =
  "block min-h-(--min-target) w-full rounded-control border border-line bg-panel px-3 py-2 text-ink placeholder:text-muted read-only:bg-surface read-only:text-muted disabled:cursor-not-allowed disabled:opacity-60 aria-invalid:border-danger-ink";

function asText(value: unknown): string {
  return typeof value === "string" ? value : typeof value === "number" ? String(value) : "";
}

function asList(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
}

/** True when `value` is one of the values the field marks as risky. */
export function isRisky(field: FieldSchema, value: unknown): boolean {
  return field.risk !== null && field.risk_when.some((risky) => risky === value);
}

function describeValue(value: unknown, t: ReturnType<typeof useT>): string {
  return typeof value === "boolean" ? (value ? t("common.on") : t("common.off")) : asText(value);
}

interface Wiring {
  id: string;
  describedBy: string | undefined;
  invalid: boolean;
  locked: boolean;
}

function Control({
  field,
  value,
  wiring,
  commit,
}: {
  field: FieldSchema;
  value: EditorValue;
  wiring: Wiring;
  commit: (value: EditorValue) => void;
}): ReactNode {
  const { id, describedBy, invalid, locked } = wiring;
  const common = {
    id,
    "aria-describedby": describedBy,
    "aria-invalid": invalid || undefined,
  };
  switch (field.kind) {
    case "bool":
      return <Switch {...common} checked={value === true} disabled={locked} onCheckedChange={commit} />;
    case "int":
    case "float":
      return (
        <Input
          {...common}
          value={asText(value)}
          readOnly={locked}
          inputMode={field.kind === "int" ? "numeric" : "decimal"}
          autoComplete="off"
          onChange={(event) => {
            commit(event.target.value);
          }}
        />
      );
    case "text":
      return field.multiline ? (
        <textarea
          {...common}
          rows={4}
          maxLength={field.max_length}
          value={asText(value)}
          readOnly={locked}
          className={cn(textClass, "resize-y")}
          onChange={(event) => {
            commit(event.target.value);
          }}
        />
      ) : (
        <Input
          {...common}
          value={asText(value)}
          readOnly={locked}
          maxLength={field.max_length}
          autoComplete="off"
          onChange={(event) => {
            commit(event.target.value);
          }}
        />
      );
    case "choice": {
      const options = field.options.map((option) => ({ value: option, label: option }));
      return <Select {...common} value={asText(value)} options={options} disabled={locked} onValueChange={commit} />;
    }
    default:
      return null;
  }
}

/**
 * One field of a section form, by its schema `kind`: label, control, help, where the value comes from, the
 * lock when the environment sets it, the warning of a risky value, the server's message and the way back to the
 * default. A kind this version does not know shows a notice instead of a control.
 */
export function FieldRenderer({ field, value, loaded, state, error, idPrefix = "", onChange, onRestore }: FieldRendererProps) {
  const t = useT();
  const base = `${idPrefix}${useId()}`;
  const [pendingRisk, setPendingRisk] = useState<EditorValue>(undefined);
  const [askRisk, setAskRisk] = useState(false);
  const [askRestore, setAskRestore] = useState(false);

  const locked = (state?.locked ?? false) || field.readonly;
  const helpId = `${base}-help`;
  const errorId = `${base}-error`;
  const lockId = `${base}-lock`;
  const hintId = `${base}-hint`;
  const hasHint = field.bounds !== null;
  const describedBy =
    [field.help ? helpId : null, hasHint ? hintId : null, locked ? lockId : null, error ? errorId : null]
      .filter((part): part is string => part !== null)
      .join(" ") || undefined;
  const wiring: Wiring = { id: base, describedBy, invalid: Boolean(error), locked };

  const known = ["bool", "int", "float", "text", "choice", "multichoice", "secret"].includes(field.kind);
  const asksFirst = field.kind === "bool" || field.kind === "choice";

  const commit = (next: EditorValue): void => {
    if (locked) return;
    if (asksFirst && isRisky(field, next) && !isRisky(field, value)) {
      setPendingRisk(next);
      setAskRisk(true);
      return;
    }
    onChange(next);
  };

  const origin = state?.origin;
  const canRestore = onRestore !== undefined && !locked && origin === "file";
  const lockText = field.env_var ? t("field.locked", { variable: field.env_var }) : t("field.lockedNoVariable");
  const showRisk = field.risk !== null && isRisky(field, value);

  let control: ReactNode;
  if (!known) {
    control = <p className="m-0 text-muted">{t("field.unsupported")}</p>;
  } else if (field.kind === "secret") {
    control = (
      <div role="group" aria-labelledby={`${base}-label`} aria-describedby={describedBy}>
        <SecretField
          label={field.label}
          value={value}
          loaded={loaded}
          id={base}
          describedBy={describedBy}
          invalid={Boolean(error)}
          disabled={locked}
          onChange={commit}
        />
      </div>
    );
  } else if (field.kind === "multichoice") {
    control = (
      <CheckboxGroup
        legend={field.label}
        options={field.options.map((option) => ({ value: option, label: option }))}
        value={asList(value)}
        disabled={locked}
        invalid={Boolean(error)}
        {...(describedBy !== undefined && { "aria-describedby": describedBy })}
        onValueChange={commit}
      />
    );
  } else if (field.kind === "choice" && field.options.length <= MAX_RADIO_OPTIONS) {
    control = (
      <RadioGroup
        legend={field.label}
        options={field.options.map((option) => ({ value: option, label: option }))}
        value={asText(value)}
        disabled={locked}
        invalid={Boolean(error)}
        {...(describedBy !== undefined && { "aria-describedby": describedBy })}
        onValueChange={commit}
      />
    );
  } else {
    control = <Control field={field} value={value} wiring={wiring} commit={commit} />;
  }

  const labelled = known && field.kind !== "multichoice" && field.kind !== "secret" && !(field.kind === "choice" && field.options.length <= MAX_RADIO_OPTIONS);
  const inline = field.kind === "bool";

  return (
    <div className="flex flex-col gap-1.5" data-field={field.key}>
      {labelled && !inline ? (
        <label htmlFor={base} className="font-medium">
          {field.label}
        </label>
      ) : null}
      {!known || field.kind === "secret" ? (
        <p id={`${base}-label`} className="m-0 font-medium">
          {field.label}
        </p>
      ) : null}
      {inline ? (
        <div className="flex min-h-(--min-target) items-center justify-between gap-4">
          <label htmlFor={base} className="min-w-0 flex-1 font-medium">
            {field.label}
          </label>
          {control}
        </div>
      ) : (
        control
      )}
      {field.help ? (
        <p id={helpId} className="m-0 text-sm text-muted">
          {field.help}
        </p>
      ) : null}
      {field.bounds ? (
        <p id={hintId} className="m-0 text-sm text-muted">
          {t("field.bounds", { min: field.bounds[0], max: field.bounds[1] })}
        </p>
      ) : null}
      {locked ? (
        <p id={lockId} className="m-0 flex items-start gap-1.5 text-sm text-muted">
          <Lock aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
          <span>{field.readonly && !state?.locked ? t("field.readonly") : lockText}</span>
        </p>
      ) : null}
      {showRisk ? (
        <p className="m-0 flex items-start gap-1.5 rounded-control bg-warning-soft px-3 py-2 text-sm text-warning-ink">
          <TriangleAlert aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
          <span>
            <span className="sr-only">{t("field.risk.warning")}{" "}</span>
            {field.risk}
          </span>
        </p>
      ) : null}
      {error ? (
        <p id={errorId} className="m-0 flex items-start gap-1.5 text-sm font-medium text-danger-ink">
          <CircleAlert aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
          <span>
            <span className="sr-only">{t("field.error")}{" "}</span>
            {error}
          </span>
        </p>
      ) : null}
      {origin ? (
        <div className="flex flex-wrap items-center gap-2">
          <Badge variant={origin === "environment" ? "warning" : origin === "file" ? "accent" : "neutral"}>
            {t(`field.origin.${origin}`)}
          </Badge>
          {canRestore ? (
            <Button
              variant="ghost"
              className="min-h-(--min-target) px-2 text-sm"
              aria-label={t("field.restoreAria", { label: field.label })}
              onClick={() => {
                setAskRestore(true);
              }}
            >
              <RotateCcw aria-hidden="true" />
              {t("field.restore")}
            </Button>
          ) : null}
        </div>
      ) : null}

      <ConfirmDialog
        open={askRisk}
        onOpenChange={setAskRisk}
        title={t("field.risk.title")}
        description={t("field.risk.description", { label: field.label, value: describeValue(pendingRisk, t) })}
        destructive
        consequence={field.risk ?? ""}
        confirmLabel={t("field.risk.confirm")}
        cancelLabel={t("common.cancel")}
        onConfirm={() => {
          onChange(pendingRisk);
        }}
      />
      <ConfirmDialog
        open={askRestore}
        onOpenChange={setAskRestore}
        title={t("field.restore.title")}
        description={t("field.restore.description", { label: field.label })}
        destructive
        consequence={t("field.restore.consequence")}
        confirmLabel={t("field.restore")}
        cancelLabel={t("common.cancel")}
        onConfirm={() => {
          onRestore?.(field.key);
        }}
      />
    </div>
  );
}
