import { Plus, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { useT } from "@/i18n/t";

/** The text the server reads for a list: one item per line, blank ones left out. */
export function joinLines(items: readonly string[]): string {
  return items.filter((item) => item.trim() !== "").join("\n");
}

/** The items a list value holds, whether the server sent an array or a text of lines. */
export function toItems(value: unknown): string[] {
  if (Array.isArray(value)) return value.filter((item): item is string => typeof item === "string");
  if (typeof value === "string") return value.split("\n");
  return [];
}

export interface ListFieldProps {
  label: string;
  value: unknown;
  id: string;
  describedBy?: string | undefined;
  invalid?: boolean;
  disabled?: boolean;
  maxItems?: number;
  onChange: (items: string[]) => void;
}

/** One input per item, with Add and Remove. Enter in an item adds the next one; a filled item asks before it goes. */
export function ListField({ label, value, id, describedBy, invalid = false, disabled = false, maxItems = Infinity, onChange }: ListFieldProps) {
  const t = useT();
  const items = toItems(value);
  const inputs = useRef<(HTMLInputElement | null)[]>([]);
  const addButton = useRef<HTMLButtonElement>(null);
  const focusAfter = useRef<number | "add" | null>(null);
  const [removing, setRemoving] = useState<number | null>(null);

  // After an add or a remove the focus goes where the operator is working, once the new rows exist.
  useEffect(() => {
    const target = focusAfter.current;
    if (target === null) return;
    focusAfter.current = null;
    (target === "add" ? addButton.current : inputs.current[target])?.focus();
  });

  const atLimit = items.length >= maxItems;

  const insertAt = (index: number): void => {
    if (atLimit) return;
    onChange([...items.slice(0, index), "", ...items.slice(index)]);
    focusAfter.current = index;
  };
  const removeAt = (index: number): void => {
    onChange(items.filter((_, position) => position !== index));
    focusAfter.current = index > 0 ? index - 1 : items.length > 1 ? 0 : "add";
  };
  const askRemove = (index: number): void => {
    if ((items[index] ?? "").trim() === "") removeAt(index);
    else setRemoving(index);
  };

  return (
    <div role="group" aria-labelledby={`${id}-label`} aria-describedby={describedBy} className="flex flex-col gap-2">
      {items.length === 0 ? <p className="m-0 text-muted">{t("list.empty")}</p> : null}
      <ul className="m-0 flex list-none flex-col gap-2 p-0">
        {items.map((item, index) => (
          // Rows have no state of their own, so the position is a stable enough key.
          <li key={index} className="flex items-center gap-2">
            <Input
              ref={(element) => {
                inputs.current[index] = element;
              }}
              id={index === 0 ? id : undefined}
              value={item}
              readOnly={disabled}
              autoComplete="off"
              aria-label={t("list.itemAria", { label, number: index + 1 })}
              aria-invalid={invalid || undefined}
              onChange={(event) => {
                onChange(items.map((existing, position) => (position === index ? event.target.value : existing)));
              }}
              onKeyDown={(event) => {
                if (event.key === "Enter" && !disabled) {
                  event.preventDefault();
                  insertAt(index + 1);
                }
              }}
            />
            <Button
              variant="ghost"
              size="icon"
              disabled={disabled}
              aria-label={t("list.removeAria", { label, number: index + 1 })}
              onClick={() => {
                askRemove(index);
              }}
            >
              <X aria-hidden="true" />
            </Button>
          </li>
        ))}
      </ul>
      <div>
        <Button
          ref={addButton}
          variant="secondary"
          disabled={disabled || atLimit}
          onClick={() => {
            insertAt(items.length);
          }}
        >
          <Plus aria-hidden="true" />
          {t("list.add")}
        </Button>
      </div>
      <ConfirmDialog
        open={removing !== null}
        onOpenChange={(open) => {
          if (!open) setRemoving(null);
        }}
        title={t("list.remove.title")}
        description={t("list.remove.description", { item: removing === null ? "" : (items[removing] ?? ""), label })}
        destructive
        consequence={t("list.remove.consequence")}
        confirmLabel={t("list.remove.confirm")}
        cancelLabel={t("common.cancel")}
        onConfirm={() => {
          if (removing !== null) removeAt(removing);
          setRemoving(null);
        }}
      />
    </div>
  );
}
