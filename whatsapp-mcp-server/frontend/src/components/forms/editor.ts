import type { FieldSchema } from "@/types/api";
import { joinLines, toItems } from "./ListField";
import { secretMarker } from "./SecretField";

/**
 * Conversions between what the server stores and what a control holds while the operator edits.
 * Text, int and float are held as the raw text typed; lists as their items; secrets as a marker, new text
 * or a clear; records as one such map per record.
 */

type Row = Record<string, unknown>;

function isRow(value: unknown): value is Row {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function rowsOf(value: unknown): Row[] {
  return Array.isArray(value) ? value.filter(isRow) : [];
}

/** The editor value for a value the server sent. An absent value reads as the field's default. */
export function editorFromValue(field: FieldSchema, value: unknown): unknown {
  const present = value === undefined ? field.default : value;
  switch (field.kind) {
    case "bool":
      return present === true;
    case "int":
    case "float":
      return typeof present === "number" ? String(present) : typeof present === "string" ? present : "";
    case "text":
      return typeof present === "string" ? present : "";
    case "choice":
      return typeof present === "string" ? present : (field.options[0] ?? "");
    case "multichoice":
      return Array.isArray(present) ? present.filter((item): item is string => typeof item === "string") : [];
    case "list":
      return toItems(present ?? []);
    case "secret":
      return secretMarker(value);
    case "records":
      return rowsOf(value ?? field.default).map((row) => blankFrom(field.fields, row));
    default:
      return present;
  }
}

function blankFrom(fields: readonly FieldSchema[], row: Row | undefined): Row {
  return Object.fromEntries(
    fields.map((sub) => [sub.key, editorFromValue(sub, row === undefined ? undefined : row[sub.key])]),
  );
}

/** A new record with every sub-field at its default. */
export function blankRecord(fields: readonly FieldSchema[]): Row {
  return blankFrom(fields, undefined);
}

/** What a field holds when it is at its default. */
export function editorDefault(field: FieldSchema): unknown {
  return field.kind === "secret" ? { set: false } : editorFromValue(field, field.default);
}

function numberOrText(kind: "int" | "float", raw: string): number | string | null {
  const text = raw.trim();
  if (text === "") return null;
  if (kind === "int") return /^[+-]?\d+$/.test(text) ? Number(text) : raw;
  return /^[+-]?(\d+([.,]\d*)?|[.,]\d+)$/.test(text) ? Number(text.replace(",", ".")) : raw;
}

/** A secret change to send, or undefined when the secret is untouched and nothing is sent. */
function secretChange(editor: unknown, inRecord: boolean): unknown {
  if (typeof editor === "string" && editor !== "") return editor;
  if (typeof editor === "object" && editor !== null && "clear" in editor) return { clear: true };
  // Inside a record an untouched secret is named so the server keeps the one of the record with the same id.
  return inRecord && secretMarker(editor).set ? { set: true } : undefined;
}

function recordValue(fields: readonly FieldSchema[], row: unknown): Row {
  const source = isRow(row) ? row : {};
  const out: Row = {};
  for (const sub of fields) {
    const sent = plainValue(sub, source[sub.key], true);
    if (sent !== undefined) out[sub.key] = sent;
  }
  return out;
}

/** The value of a field inside a record, in the plain form the server stores. */
function plainValue(field: FieldSchema, editor: unknown, inRecord: boolean): unknown {
  switch (field.kind) {
    case "int":
    case "float":
      return numberOrText(field.kind, typeof editor === "string" ? editor : "");
    case "text":
      return typeof editor === "string" ? editor.trim() : "";
    case "list":
      return toItems(editor)
        .map((item) => item.trim())
        .filter((item) => item !== "");
    case "secret":
      return secretChange(editor, inRecord);
    case "records":
      return (Array.isArray(editor) ? editor : []).map((row: unknown) => recordValue(field.fields, row));
    default:
      return editor;
  }
}

/**
 * What goes in `changes` for a field: the raw text for text, int and float, the lines for a list, the value
 * itself for the rest. Undefined means send nothing (an untouched secret).
 */
export function changeFor(field: FieldSchema, editor: unknown): unknown {
  switch (field.kind) {
    case "int":
    case "float":
    case "text":
      return typeof editor === "string" ? editor : "";
    case "list":
      return joinLines(toItems(editor));
    case "secret":
      return secretChange(editor, false);
    default:
      return plainValue(field, editor, false);
  }
}
