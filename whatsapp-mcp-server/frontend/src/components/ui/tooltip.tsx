import { Tooltip as TooltipPrimitive } from "radix-ui";
import type { ReactElement, ReactNode } from "react";

/** Shares the open delay of every tooltip below it; zero, so the name shows as soon as the icon is hovered or focused. */
export function TooltipProvider({ children }: { children: ReactNode }) {
  return (
    <TooltipPrimitive.Provider delayDuration={0}>
      {children}
    </TooltipPrimitive.Provider>
  );
}

/**
 * Shows `label` beside its child on hover and on keyboard focus. The child keeps its own accessible name: the
 * tooltip repeats it for the eye and adds nothing the screen reader needs.
 */
export function Tooltip({
  label,
  side = "right",
  children,
}: {
  label: string;
  side?: "top" | "right" | "bottom" | "left";
  children: ReactElement;
}) {
  return (
    <TooltipPrimitive.Root>
      <TooltipPrimitive.Trigger asChild>{children}</TooltipPrimitive.Trigger>
      <TooltipPrimitive.Portal>
        <TooltipPrimitive.Content
          side={side}
          sideOffset={8}
          className="z-60 max-w-64 rounded-control bg-ink px-3 py-1.5 text-sm text-surface shadow-lg"
        >
          {label}
        </TooltipPrimitive.Content>
      </TooltipPrimitive.Portal>
    </TooltipPrimitive.Root>
  );
}
