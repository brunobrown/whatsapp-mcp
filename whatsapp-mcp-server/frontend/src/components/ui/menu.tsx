import { DropdownMenu as MenuPrimitive } from "radix-ui";
import type { ComponentProps } from "react";
import { cn } from "@/lib/utils";

export const Menu = MenuPrimitive.Root;
export const MenuTrigger = MenuPrimitive.Trigger;

/** The popup of a menu button: arrow keys move, Enter chooses, Escape closes and returns the focus to the button. */
export function MenuContent({ className, sideOffset = 6, ...props }: ComponentProps<typeof MenuPrimitive.Content>) {
  return (
    <MenuPrimitive.Portal>
      <MenuPrimitive.Content
        align="end"
        sideOffset={sideOffset}
        className={cn(
          "z-50 min-w-48 rounded-control border border-line bg-panel p-1 text-ink shadow-xl",
          className,
        )}
        {...props}
      />
    </MenuPrimitive.Portal>
  );
}

export function MenuItem({ className, ...props }: ComponentProps<typeof MenuPrimitive.Item>) {
  return (
    <MenuPrimitive.Item
      className={cn(
        "flex min-h-(--min-target) cursor-pointer items-center gap-2 rounded-control px-3 py-2 text-[0.9375rem] outline-none select-none",
        "data-[disabled]:cursor-not-allowed data-[disabled]:opacity-50 data-[highlighted]:bg-hover",
        "[&_svg]:size-[1.125rem] [&_svg]:shrink-0",
        className,
      )}
      {...props}
    />
  );
}
