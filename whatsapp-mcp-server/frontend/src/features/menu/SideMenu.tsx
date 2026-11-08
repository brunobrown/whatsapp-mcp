import { Link } from "@tanstack/react-router";
import { useId, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Tooltip, TooltipProvider } from "@/components/ui/tooltip";
import { useT } from "@/i18n/t";
import { cn } from "@/lib/utils";
import type { SectionSummary } from "@/types/api";
import { FALLBACK_ICON, ICONS } from "./icons";
import { FILTER_FROM, filterEntries, menuGroups, pathOf } from "./model";

export type SideMenuMode = "expanded" | "rail";

export interface SideMenuProps {
  entries: readonly SectionSummary[];
  mode: SideMenuMode;
  /** Whether a restart waits for the entry's section. */
  isPending: (entry: SectionSummary) => boolean;
  /** Called after an entry was chosen, e.g. to close the drawer. */
  onNavigate?: () => void;
}

const ROW =
  "text-ink no-underline hover:bg-hover aria-[current=page]:bg-selected focus-visible:outline-offset-[-2px]";

function Entry({
  entry,
  mode,
  pending,
  onNavigate,
}: {
  entry: SectionSummary;
  mode: SideMenuMode;
  pending: boolean;
  onNavigate?: () => void;
}) {
  const t = useT();
  const Icon = ICONS[entry.icon] ?? FALLBACK_ICON;
  const rail = mode === "rail";
  const icon = (
    <span className="relative flex size-10 shrink-0 items-center justify-center rounded-full bg-accent-soft text-accent-ink">
      <Icon aria-hidden="true" className="size-5" />
      {rail && pending ? (
        <span
          aria-hidden="true"
          data-dot="restart"
          className="absolute top-0 right-0 size-3 rounded-full border-2 border-panel bg-warning-ink"
        />
      ) : null}
    </span>
  );
  const click = () => {
    onNavigate?.();
  };
  const link = rail ? (
    <Link
      to={pathOf(entry)}
      aria-label={
        pending ? t("menu.entryPending", { title: entry.title }) : entry.title
      }
      className={cn(
        "mx-auto flex size-12 items-center justify-center rounded-control",
        ROW,
      )}
      onClick={click}
    >
      {icon}
    </Link>
  ) : (
    <Link
      to={pathOf(entry)}
      className={cn("flex min-h-16 items-center gap-3 px-4 py-2.5", ROW)}
      onClick={click}
    >
      {icon}
      <span className="min-w-0 flex-1">
        <span className="block truncate font-medium" title={entry.title}>
          {entry.title}
        </span>
        {entry.description ? (
          <span
            className="block truncate text-sm text-muted"
            title={entry.description}
          >
            {entry.description}
          </span>
        ) : null}
      </span>
      {pending ? (
        <Badge variant="warning">{t("shell.pendingRestart")}</Badge>
      ) : null}
    </Link>
  );
  return rail ? (
    <Tooltip
      label={
        pending ? t("menu.entryPending", { title: entry.title }) : entry.title
      }
    >
      {link}
    </Tooltip>
  ) : (
    link
  );
}

/**
 * The sections under their group headings. Expanded shows icon, title, description and the restart badge, and a
 * filter field when there are more than eight entries; the rail shows the icons alone, each named and with a tooltip.
 */
export function SideMenu({
  entries,
  mode,
  isPending,
  onNavigate,
}: SideMenuProps) {
  const t = useT();
  const filterId = useId();
  const [text, setText] = useState("");
  const rail = mode === "rail";
  const filterable = !rail && entries.length >= FILTER_FROM;
  const shown = filterable ? filterEntries(entries, text) : entries;
  const groups = menuGroups(shown);
  return (
    <TooltipProvider>
      <nav aria-label={t("shell.nav")} className="flex min-h-0 flex-1 flex-col">
        {filterable ? (
          <div className="shrink-0 border-b border-line p-3">
            <input
              id={filterId}
              type="search"
              value={text}
              aria-label={t("menu.filter")}
              placeholder={t("menu.filter")}
              onChange={(event) => {
                setText(event.target.value);
              }}
              className="min-h-(--min-target) w-full rounded-control border border-line bg-panel px-3 text-ink placeholder:text-muted"
            />
          </div>
        ) : null}
        <div
          data-menu-scroll
          className="min-h-0 flex-1 overflow-x-hidden overflow-y-auto overscroll-contain"
        >
          {filterable && shown.length === 0 ? (
            <div role="status" className="flex flex-col items-start gap-3 p-4">
              <p className="m-0 text-muted">{t("menu.filter.empty")}</p>
              <Button
                variant="secondary"
                onClick={() => {
                  setText("");
                }}
              >
                {t("menu.filter.clear")}
              </Button>
            </div>
          ) : (
            groups.map((group) => (
              <section
                key={group.id}
                aria-labelledby={`${filterId}-${group.id}`}
                className={cn(rail && "border-b border-line py-1")}
              >
                <h2
                  id={`${filterId}-${group.id}`}
                  className={cn(
                    "m-0 px-4 pt-3 pb-1 text-xs font-semibold tracking-wide text-muted uppercase",
                    rail && "sr-only",
                  )}
                >
                  {t(group.titleKey)}
                </h2>
                <ul className="m-0 flex list-none flex-col p-0">
                  {group.entries.map((entry) => (
                    <li
                      key={entry.id}
                      className={cn(!rail && "border-b border-line")}
                    >
                      <Entry
                        entry={entry}
                        mode={mode}
                        pending={isPending(entry)}
                        {...(onNavigate && { onNavigate })}
                      />
                    </li>
                  ))}
                </ul>
              </section>
            ))
          )}
        </div>
      </nav>
    </TooltipProvider>
  );
}
