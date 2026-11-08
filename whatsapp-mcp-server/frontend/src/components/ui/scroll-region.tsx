import { useCallback, useLayoutEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

export interface ScrollRegionProps {
  /** Accessible name; announced and used for keyboard focus once the content scrolls. */
  label: string;
  children: ReactNode;
  /** Draws a border and a rounded corner around the region. */
  bordered?: boolean;
  /** Content that stays on top while the rest scrolls, such as a table header row. */
  header?: ReactNode;
  className?: string;
  /** Classes of the scrolling box, e.g. a maximum height. */
  scrollerClassName?: string;
  id?: string;
}

interface Cut {
  scrollable: boolean;
  top: boolean;
  bottom: boolean;
}

const FIT: Cut = { scrollable: false, top: false, bottom: false };
const EDGE = 1;

/**
 * A box that scrolls inside itself. While its content overflows it is a focusable named region, so the keyboard
 * can scroll it, and a shadow marks each edge that cuts content (data-top and data-bottom say which).
 */
export function ScrollRegion({ label, children, bordered, header, className, scrollerClassName, id }: ScrollRegionProps) {
  const scroller = useRef<HTMLDivElement>(null);
  const [cut, setCut] = useState<Cut>(FIT);

  const measure = useCallback(() => {
    const el = scroller.current;
    if (!el) return;
    const vertical = el.scrollHeight - el.clientHeight > EDGE;
    // Sideways overflow also makes the region something the keyboard must reach; only the vertical edges get a shadow.
    const next: Cut = {
      scrollable: vertical || el.scrollWidth - el.clientWidth > EDGE,
      top: vertical && el.scrollTop > EDGE,
      bottom: vertical && el.scrollTop + el.clientHeight < el.scrollHeight - EDGE,
    };
    setCut((previous) =>
      previous.scrollable === next.scrollable && previous.top === next.top && previous.bottom === next.bottom ? previous : next,
    );
  }, []);

  useLayoutEffect(() => {
    measure();
    const el = scroller.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(measure);
    observer.observe(el);
    for (const child of Array.from(el.children)) observer.observe(child);
    return () => {
      observer.disconnect();
    };
  });

  return (
    <div id={id} className={cn("relative min-h-0 max-w-full overflow-hidden", bordered && "rounded-card border border-line", className)}>
      <div
        ref={scroller}
        {...(cut.scrollable ? { role: "region", tabIndex: 0, "aria-label": label } : {})}
        data-top={cut.top}
        data-bottom={cut.bottom}
        onScroll={measure}
        className={cn("max-h-full overflow-auto overscroll-contain", scrollerClassName)}
      >
        {header ? <div className="sticky top-0 z-10 bg-panel">{header}</div> : null}
        {children}
      </div>
      {cut.top ? (
        <div aria-hidden="true" data-edge="top" className="pointer-events-none absolute inset-x-0 top-0 h-3 bg-linear-to-b from-ink/20 to-transparent" />
      ) : null}
      {cut.bottom ? (
        <div aria-hidden="true" data-edge="bottom" className="pointer-events-none absolute inset-x-0 bottom-0 h-3 bg-linear-to-t from-ink/20 to-transparent" />
      ) : null}
    </div>
  );
}
