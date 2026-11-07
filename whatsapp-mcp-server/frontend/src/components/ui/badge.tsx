import { cva } from "class-variance-authority";
import type { VariantProps } from "class-variance-authority";
import type { ComponentProps } from "react";
import { cn } from "@/lib/utils";

const badgeVariants = cva(
  "inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs font-medium whitespace-nowrap",
  {
    variants: {
      variant: {
        neutral: "bg-selected text-muted",
        accent: "bg-accent-soft text-accent-ink",
        warning: "bg-warning-soft text-warning-ink",
        danger: "bg-danger-soft text-danger-ink",
      },
    },
    defaultVariants: { variant: "neutral" },
  },
);

export type BadgeProps = ComponentProps<"span"> & VariantProps<typeof badgeVariants>;

export function Badge({ className, variant, ...props }: BadgeProps) {
  return <span data-variant={variant ?? "neutral"} className={cn(badgeVariants({ variant }), className)} {...props} />;
}
