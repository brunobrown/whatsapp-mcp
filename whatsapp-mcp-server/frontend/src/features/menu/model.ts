import type { MessageKey } from "@/i18n/en";
import type { SectionSummary } from "@/types/api";

export type MenuGroupId =
  "overview" | "messaging" | "safety" | "preferences" | "more";

export interface MenuGroup {
  id: MenuGroupId;
  /** Catalog key of the heading. */
  titleKey: Extract<MessageKey, `menu.group.${string}`>;
  entries: SectionSummary[];
}

const GROUPS: readonly { id: MenuGroupId; titleKey: MenuGroup["titleKey"] }[] =
  [
    { id: "overview", titleKey: "menu.group.overview" },
    { id: "messaging", titleKey: "menu.group.messaging" },
    { id: "safety", titleKey: "menu.group.safety" },
    { id: "preferences", titleKey: "menu.group.preferences" },
    { id: "more", titleKey: "menu.group.more" },
  ];

/** The group of each known section; any other id lands in More. */
const GROUP_OF: Readonly<Record<string, MenuGroupId>> = {
  status: "overview",
  activity: "overview",
  send_policy: "messaging",
  monitor: "messaging",
  webhooks: "messaging",
  channel: "messaging",
  agent_safety: "safety",
  database: "safety",
  media: "safety",
  privacy: "safety",
  timezone: "preferences",
  interface: "preferences",
  account: "preferences",
};

/** A menu with more entries than this gets a filter field. */
export const FILTER_FROM = 9;

/** Entries under their headings in a fixed order, keeping the incoming order inside a group; an empty group is left out. */
export function menuGroups(entries: readonly SectionSummary[]): MenuGroup[] {
  return GROUPS.map((group) => ({
    ...group,
    entries: entries.filter(
      (entry) => (GROUP_OF[entry.id] ?? "more") === group.id,
    ),
  })).filter((group) => group.entries.length > 0);
}

/** The entries whose title or description contains the text, ignoring case; a blank text keeps everything. */
export function filterEntries(
  entries: readonly SectionSummary[],
  text: string,
): SectionSummary[] {
  const needle = text.trim().toLowerCase();
  if (needle === "") return [...entries];
  return entries.filter((entry) =>
    `${entry.title}\n${entry.description}`.toLowerCase().includes(needle),
  );
}

/** The path of an entry: a section lives under /s, a page at its own id. */
export function pathOf(entry: SectionSummary): string {
  return entry.kind === "section" ? `/s/${entry.id}` : `/${entry.id}`;
}
