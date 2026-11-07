import { Plus, Trash2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/dialog";
import { useT } from "@/i18n/t";
import type { FieldSchema } from "@/types/api";
import { blankRecord } from "./editor";
import { FieldRenderer } from "./FieldRenderer";

type Row = Record<string, unknown>;

function rowsOf(value: unknown): Row[] {
  return Array.isArray(value) ? value.filter((row): row is Row => typeof row === "object" && row !== null && !Array.isArray(row)) : [];
}

export interface RecordsFieldProps {
  field: FieldSchema;
  /** The records as edited: one map of sub-field editor values per record. */
  value: unknown;
  /** The records as the server sent them; secrets read their marker from the record with the same id. */
  loaded?: unknown;
  /** Messages by dotted key, such as `profiles.0.targets`, for the whole section. */
  errors?: Readonly<Record<string, string>>;
  id: string;
  /** The dotted path of this field, `profiles` for a top-level field. */
  path?: string;
  disabled?: boolean;
  onChange: (records: Row[]) => void;
}

/** What names a record on its card and in its dialogs: its id sub-field, else its position. */
function nameOf(field: FieldSchema, row: Row, index: number, fallback: (number: number) => string): string {
  const named = field.id_key === null ? undefined : row[field.id_key];
  return typeof named === "string" && named.trim() !== "" ? named.trim() : fallback(index + 1);
}

/**
 * Repeated cards for a `records` field. Each card shows the sub-fields through FieldRenderer; Add makes a blank
 * record, Remove asks first. A card keeps its identity when another is removed, so its secret stays its own.
 */
export function RecordsField({ field, value, loaded, errors = {}, id, path = field.key, disabled = false, onChange }: RecordsFieldProps) {
  const t = useT();
  const records = rowsOf(value);
  const stored = rowsOf(loaded);
  const [ids, setIds] = useState<number[]>(() => records.map((_, index) => index));
  const nextId = useRef(records.length);
  const [removing, setRemoving] = useState<number | null>(null);
  const cards = useRef(new Map<number, HTMLElement>());
  const focusCard = useRef<number | null>(null);

  // When the records were replaced from outside (a reload, a discard) the identities start over.
  const keys = ids.length === records.length ? ids : records.map((_, index) => -1 - index);

  useEffect(() => {
    const target = focusCard.current;
    if (target === null) return;
    focusCard.current = null;
    cards.current.get(target)?.focus();
  });

  const atLimit = records.length >= field.max_items;
  const add = (): void => {
    if (atLimit) return;
    const key = nextId.current++;
    setIds([...keys, key]);
    focusCard.current = key;
    onChange([...records, blankRecord(field.fields)]);
  };
  const remove = (index: number): void => {
    setIds(keys.filter((_, position) => position !== index));
    onChange(records.filter((_, position) => position !== index));
  };
  const change = (index: number, key: string, next: unknown): void => {
    onChange(records.map((row, position) => (position === index ? { ...row, [key]: next } : row)));
  };

  const whole = errors[path];
  const untitled = (number: number): string => t("records.title", { label: field.label, number });
  const removingName = removing === null ? "" : nameOf(field, records[removing] ?? {}, removing, untitled);

  return (
    <div className="flex flex-col gap-3">
      {whole ? <p className="m-0 font-medium text-danger-ink">{whole}</p> : null}
      {records.length === 0 ? <p className="m-0 text-muted">{t("records.empty")}</p> : null}
      {records.map((row, index) => {
        const key = keys[index] ?? index;
        const name = nameOf(field, row, index, untitled);
        const mine = field.id_key === null ? undefined : stored.find((record) => record[field.id_key ?? ""] === row[field.id_key ?? ""]);
        const headingId = `${id}-card-${String(key)}`;
        return (
          <section
            key={key}
            ref={(element) => {
              if (element) cards.current.set(key, element);
              else cards.current.delete(key);
            }}
            tabIndex={-1}
            aria-labelledby={headingId}
            className="flex flex-col gap-4 rounded-card border border-line bg-panel p-4 focus-visible:outline-offset-2"
          >
            <div className="flex items-center justify-between gap-2">
              <h3 id={headingId} className="m-0 min-w-0 truncate text-base font-semibold">
                {name}
              </h3>
              <Button
                variant="ghost"
                disabled={disabled}
                aria-label={t("records.removeAria", { name })}
                onClick={() => {
                  setRemoving(index);
                }}
              >
                <Trash2 aria-hidden="true" />
                {t("records.remove")}
              </Button>
            </div>
            {errors[`${path}.${String(index)}`] ? (
              <p className="m-0 font-medium text-danger-ink">{errors[`${path}.${String(index)}`]}</p>
            ) : null}
            {field.fields.map((sub) => (
              <FieldRenderer
                key={sub.key}
                field={disabled ? { ...sub, readonly: true } : sub}
                value={row[sub.key]}
                loaded={mine?.[sub.key]}
                error={errors[`${path}.${String(index)}.${sub.key}`]}
                idPrefix={`${id}-${String(key)}-`}
                onChange={(next) => {
                  change(index, sub.key, next);
                }}
              />
            ))}
          </section>
        );
      })}
      <div>
        <Button variant="secondary" disabled={disabled || atLimit} aria-label={t("records.addAria", { label: field.label })} onClick={add}>
          <Plus aria-hidden="true" />
          {t("records.add")}
        </Button>
      </div>
      <ConfirmDialog
        open={removing !== null}
        onOpenChange={(open) => {
          if (!open) setRemoving(null);
        }}
        title={t("records.remove.title")}
        description={t("records.remove.description", { name: removingName, label: field.label })}
        destructive
        consequence={t("records.remove.consequence")}
        confirmLabel={t("records.remove")}
        cancelLabel={t("common.cancel")}
        onConfirm={() => {
          if (removing !== null) remove(removing);
          setRemoving(null);
        }}
      />
    </div>
  );
}
