import { Dialog as DialogPrimitive } from "radix-ui";
import { useRef } from "react";
import type { ComponentProps, PointerEvent } from "react";
import { cn } from "@/lib/utils";

export const Sheet = DialogPrimitive.Root;
export const SheetTrigger = DialogPrimitive.Trigger;
export const SheetClose = DialogPrimitive.Close;

/** A swipe to the left past this share of the sheet's width closes it. */
export const SWIPE_CLOSE_SHARE = 1 / 3;

export interface SheetContentProps extends Omit<ComponentProps<typeof DialogPrimitive.Content>, "title"> {
  /** Accessible name of the sheet. */
  label: string;
  onOpenChange: (open: boolean) => void;
}

/**
 * A modal panel that slides in from the left, at most 320 px and 85% of the width, over a dimmed overlay. The
 * dialog primitive traps focus, closes on Escape and on a tap of the overlay, keeps the page behind it from scrolling
 * and hides it from assistive technology. A touch swipe to the left past a third of the width closes it too.
 */
export function SheetContent({ label, onOpenChange, className, children, ...props }: SheetContentProps) {
  const panel = useRef<HTMLDivElement>(null);
  const swipe = useRef<{ id: number; x: number; y: number; dx: number; locked: boolean } | null>(null);

  const reset = () => {
    swipe.current = null;
    if (panel.current) panel.current.style.transform = "";
  };
  const down = (event: PointerEvent<HTMLDivElement>) => {
    if (event.pointerType === "mouse") return;
    swipe.current = { id: event.pointerId, x: event.clientX, y: event.clientY, dx: 0, locked: false };
  };
  const move = (event: PointerEvent<HTMLDivElement>) => {
    const start = swipe.current;
    if (start?.id !== event.pointerId) return;
    const dx = event.clientX - start.x;
    const dy = event.clientY - start.y;
    // A drag that is mostly vertical is the menu scrolling, not a swipe.
    if (!start.locked && Math.abs(dy) > Math.abs(dx)) return;
    start.locked = true;
    start.dx = Math.min(0, dx);
    if (panel.current) panel.current.style.transform = `translateX(${String(start.dx)}px)`;
  };
  const up = (event: PointerEvent<HTMLDivElement>) => {
    const start = swipe.current;
    if (start?.id !== event.pointerId) return;
    const width = panel.current?.offsetWidth ?? 0;
    const close = start.locked && -start.dx > width * SWIPE_CLOSE_SHARE;
    reset();
    if (close) onOpenChange(false);
  };

  return (
    <DialogPrimitive.Portal>
      <DialogPrimitive.Overlay
        data-sheet-overlay=""
        className="fixed inset-0 z-50 bg-[#0b141a]/60 data-[state=closed]:animate-[sheet-fade-out_150ms_ease-in] data-[state=open]:animate-[sheet-fade-in_200ms_ease-out]"
      />
      <DialogPrimitive.Content
        ref={panel}
        aria-describedby={undefined}
        onPointerDown={down}
        onPointerMove={move}
        onPointerUp={up}
        onPointerCancel={reset}
        style={{ touchAction: "pan-y" }}
        className={cn(
          "fixed inset-y-0 left-0 z-50 flex w-[min(320px,85vw)] flex-col overflow-hidden bg-panel pt-(--safe-top) pb-(--safe-bottom) pl-(--safe-left) text-ink shadow-xl",
          "data-[state=closed]:animate-[sheet-out_150ms_ease-in] data-[state=open]:animate-[sheet-in_200ms_ease-out]",
          className,
        )}
        {...props}
      >
        <DialogPrimitive.Title asChild>
          <span className="sr-only">{label}</span>
        </DialogPrimitive.Title>
        {children}
      </DialogPrimitive.Content>
    </DialogPrimitive.Portal>
  );
}
