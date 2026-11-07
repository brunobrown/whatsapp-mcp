import type { ComponentProps } from "react";
import { cn } from "@/lib/utils";

/** A text field. Name it with a <label> or aria-label; wire errors with aria-invalid and aria-describedby. */
export function Input({ className, type = "text", ...props }: ComponentProps<"input">) {
  return (
    <input
      type={type}
      className={cn(
        "block min-h-(--min-target) w-full rounded-control border border-line bg-panel px-3 text-ink",
        "placeholder:text-muted",
        "read-only:bg-surface read-only:text-muted",
        "disabled:cursor-not-allowed disabled:opacity-60",
        "aria-invalid:border-danger-ink",
        className,
      )}
      {...props}
    />
  );
}
