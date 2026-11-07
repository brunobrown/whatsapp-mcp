import { cva } from "class-variance-authority";
import type { VariantProps } from "class-variance-authority";
import { Slot } from "radix-ui";
import type { ComponentProps, MouseEvent } from "react";
import { cn } from "@/lib/utils";

export const buttonVariants = cva(
  [
    "inline-flex min-h-(--min-target) shrink-0 cursor-pointer items-center justify-center gap-2 rounded-control",
    "px-4 text-[0.9375rem] font-medium whitespace-nowrap select-none",
    "transition-colors duration-150",
    "aria-disabled:cursor-progress aria-disabled:opacity-70 disabled:cursor-not-allowed disabled:opacity-50",
    "[&_svg]:pointer-events-none [&_svg]:size-[1.125rem] [&_svg]:shrink-0",
  ],
  {
    variants: {
      variant: {
        primary: "bg-accent-solid text-on-accent hover:bg-accent-solid-hover aria-disabled:hover:bg-accent-solid",
        secondary: "border border-line bg-panel text-ink hover:bg-hover",
        ghost: "text-ink hover:bg-hover",
        danger: "bg-danger-ink text-panel hover:brightness-95",
      },
      size: {
        default: "",
        icon: "w-(--min-target) px-0",
      },
    },
    defaultVariants: { variant: "primary", size: "default" },
  },
);

export interface ButtonProps extends ComponentProps<"button">, VariantProps<typeof buttonVariants> {
  /** Render the child (a link, for instance) with the button look. */
  asChild?: boolean;
  /** Work in progress: announced as busy, kept focusable, ignores activation. */
  busy?: boolean;
}

export function Button({
  className,
  variant,
  size,
  asChild = false,
  busy = false,
  type = "button",
  onClick,
  ...props
}: ButtonProps) {
  const Component = asChild ? Slot.Root : "button";
  const handleClick = (event: MouseEvent<HTMLButtonElement>): void => {
    if (busy) {
      event.preventDefault();
      return;
    }
    onClick?.(event);
  };
  return (
    <Component
      type={asChild ? undefined : type}
      className={cn(buttonVariants({ variant, size }), className)}
      aria-busy={busy || undefined}
      aria-disabled={busy || undefined}
      onClick={handleClick}
      {...props}
    />
  );
}
