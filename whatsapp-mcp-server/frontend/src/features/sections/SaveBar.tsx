import { forwardRef, useEffect } from "react";
import { Button } from "@/components/ui/button";
import { useT } from "@/i18n/t";

export interface SaveBarProps {
  /** The id of the form the Save button submits (the bar may sit outside it). */
  formId: string;
  saving: boolean;
  onDiscard: () => void;
}

/**
 * Keeps `--keyboard-offset` at the height the on-screen keyboard covers, for browsers that do not shrink the page for it
 * (iOS Safari). Where the page does shrink (`interactive-widget=resizes-content`), the visual viewport equals the
 * window and the offset stays 0.
 */
function useKeyboardOffset(): void {
  useEffect(() => {
    const viewport = window.visualViewport;
    // Old browsers have none (the property is then undefined, not null).
    if (!viewport) return;
    const root = document.documentElement;
    const update = (): void => {
      const covered = Math.max(0, window.innerHeight - viewport.height - viewport.offsetTop);
      root.style.setProperty("--keyboard-offset", `${String(Math.round(covered))}px`);
    };
    update();
    viewport.addEventListener("resize", update);
    viewport.addEventListener("scroll", update);
    return () => {
      viewport.removeEventListener("resize", update);
      viewport.removeEventListener("scroll", update);
      root.style.removeProperty("--keyboard-offset");
    };
  }, []);
}

/**
 * The bar a form shows only while it has unsaved edits: what is pending, Discard and Save. On a phone it is fixed to the
 * bottom of the screen (index.css), above the keyboard and the home indicator, with a spacer so the content can scroll
 * clear of it.
 */
export const SaveBar = forwardRef<HTMLDivElement, SaveBarProps>(function SaveBar({ formId, saving, onDiscard }, ref) {
  const t = useT();
  useKeyboardOffset();
  return (
    <div ref={ref}>
      <div data-save-bar className="flex flex-wrap items-center gap-2 border-t border-line bg-surface px-4 py-3 sm:px-6">
        <p role="status" className="m-0 min-w-0 flex-1 basis-40 text-sm font-medium">
          {t("section.unsaved")}
        </p>
        <Button variant="secondary" disabled={saving} onClick={onDiscard}>
          {t("section.discard")}
        </Button>
        <Button type="submit" form={formId} busy={saving}>
          {t("common.save")}
        </Button>
      </div>
      <div data-save-bar-spacer aria-hidden="true" />
    </div>
  );
});
