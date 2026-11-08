import type { ReactNode } from "react";
import { ScrollRegion } from "@/components/ui/scroll-region";
import { cn } from "@/lib/utils";

export interface DataTableProps {
  /** Names the scrolling region and the table for assistive technology. */
  label: string;
  columns: readonly string[];
  children: ReactNode;
  className?: string;
}

/**
 * A table that scrolls inside its own box (24 rem tall at most, sideways when it is wide), so a long or wide one never
 * stretches the page. The header row stays in view while the rows scroll (index.css), and under 600 px the first column
 * does too. The box takes focus once it scrolls, which lets a keyboard operator use the arrow keys.
 */
export function DataTable({ label, columns, children, className }: DataTableProps) {
  return (
    <ScrollRegion label={label} bordered className={className} scrollerClassName="max-h-[24rem]">
      <table data-table className="w-full border-separate border-spacing-0 text-left text-sm">
        <caption className="sr-only">{label}</caption>
        <thead>
          <tr>
            {columns.map((column) => (
              <th key={column} scope="col" className="border-b border-line px-3 py-2 font-semibold whitespace-nowrap text-muted">
                {column}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>{children}</tbody>
      </table>
    </ScrollRegion>
  );
}

export function Cell({ children, className }: { children: ReactNode; className?: string }) {
  return <td className={cn("border-b border-line px-3 py-2 align-top whitespace-nowrap", className)}>{children}</td>;
}
