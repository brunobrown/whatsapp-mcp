import { t } from "@/i18n/t";
import type { FieldSchema } from "@/types/api";

export interface FieldGroup {
  /** Stable key of the card: the group label, `general`, or the key of a field that stands alone. */
  id: string;
  title: string;
  fields: FieldSchema[];
  /** A records or list field standing alone on its own card. */
  solo: boolean;
}

const GENERAL = "general";

function standsAlone(field: FieldSchema): boolean {
  return field.kind === "records" || field.kind === "list";
}

/**
 * The cards of a section form. A field goes in the card headed by its `group` label; fields without a label go in
 * the first card, "General"; a records or list field is a card by itself, titled by its label. Cards keep the order
 * in which their first field appears, except that "General" comes first. A section with no labels is one card.
 */
export function groupFields(fields: readonly FieldSchema[]): FieldGroup[] {
  const cards: FieldGroup[] = [];
  const byId = new Map<string, FieldGroup>();
  for (const field of fields) {
    if (standsAlone(field)) {
      cards.push({ id: `field:${field.key}`, title: field.label, fields: [field], solo: true });
      continue;
    }
    const label = field.group?.trim() ?? "";
    const id = label === "" ? GENERAL : `group:${label}`;
    let card = byId.get(id);
    if (card === undefined) {
      card = { id, title: label === "" ? t("card.general") : label, fields: [], solo: false };
      byId.set(id, card);
      cards.push(card);
    }
    card.fields.push(field);
  }
  const general = byId.get(GENERAL);
  return general === undefined ? cards : [general, ...cards.filter((card) => card !== general)];
}
