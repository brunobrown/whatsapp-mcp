import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

export interface DataTableProps {
  /** Names the scrolling region and the table for assistive technology. */
  label: string;
  columns: readonly string[];
  children: ReactNode;
  className?: string;
}

/**
 * A table that scrolls inside its own box, so a wide one never widens the page. The box takes focus, which lets a
 * keyboard operator scroll it with the arrow keys.
 */
export function DataTable({ label, columns, children, className }: DataTableProps) {
  return (
    <div
      role="region"
      aria-label={label}
      // eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex -- a scrollable region must be reachable by keyboard
      tabIndex={0}
      className={cn("max-w-full overflow-x-auto rounded-control border border-line bg-panel", className)}
    >
      <table className="w-full border-collapse text-left text-sm">
        <caption className="sr-only">{label}</caption>
        <thead>
          <tr className="border-b border-line bg-surface">
            {columns.map((column) => (
              <th key={column} scope="col" className="px-3 py-2 font-semibold whitespace-nowrap text-muted">
                {column}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>{children}</tbody>
      </table>
    </div>
  );
}

export function Cell({ children, className }: { children: ReactNode; className?: string }) {
  return <td className={cn("border-b border-line px-3 py-2 align-top whitespace-nowrap", className)}>{children}</td>;
}
