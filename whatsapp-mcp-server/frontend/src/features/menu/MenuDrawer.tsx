import { Menu, X } from "lucide-react";
import { useRouter } from "@tanstack/react-router";
import { useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Sheet, SheetClose, SheetContent, SheetTrigger } from "@/components/ui/sheet";
import { useT } from "@/i18n/t";
import { useUiStore } from "@/stores/ui";
import type { SectionSummary } from "@/types/api";
import { MenuBody } from "./MenuBody";
import { pathOf } from "./model";
import { MenuTools } from "./MenuTools";

/**
 * The menu on a phone: the "Open menu" button of the section header and the drawer it opens. Choosing an entry
 * closes the drawer and opens that section; focus returns to the button.
 */
export function MenuDrawer() {
  const t = useT();
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const stopWaiting = useRef<() => void>(() => undefined);
  useEffect(() => () => {
    stopWaiting.current();
  }, []);

  // With unsaved edits the leave guard asks first; the drawer stays until the question is answered, whichever way.
  const choose = (entry: SectionSummary) => {
    stopWaiting.current();
    if (!useUiStore.getState().draftDirty || pathOf(entry) === router.state.location.pathname) {
      setOpen(false);
      return;
    }
    let asked = useUiStore.getState().leaveAsking;
    const close = () => {
      stopWaiting.current();
      setOpen(false);
    };
    const unsubscribeStore = useUiStore.subscribe((state) => {
      if (state.leaveAsking) asked = true;
      else if (asked) close();
    });
    // A move that the guard let through (nothing was asked) also ends the wait.
    const unsubscribeRouter = router.subscribe("onResolved", () => {
      if (!useUiStore.getState().leaveAsking) close();
    });
    stopWaiting.current = () => {
      unsubscribeStore();
      unsubscribeRouter();
      stopWaiting.current = () => undefined;
    };
  };
  return (
    <Sheet open={open} onOpenChange={setOpen}>
      <SheetTrigger asChild>
        <Button variant="ghost" size="icon" aria-label={t("menu.open")}>
          <Menu aria-hidden="true" />
        </Button>
      </SheetTrigger>
      <SheetContent label={t("menu.drawer")} onOpenChange={setOpen}>
        <div className="flex h-16 shrink-0 items-center gap-1 border-b border-line bg-surface pr-2 pl-4">
          <div className="min-w-0 flex-1 leading-tight">
            <p className="m-0 truncate text-lg font-semibold">{t("app.name")}</p>
            <p className="m-0 truncate text-sm text-muted">{t("app.settings")}</p>
          </div>
          <MenuTools />
          <SheetClose asChild>
            <Button variant="ghost" size="icon" aria-label={t("menu.close")}>
              <X aria-hidden="true" />
            </Button>
          </SheetClose>
        </div>
        <MenuBody mode="expanded" onNavigate={choose} />
      </SheetContent>
    </Sheet>
  );
}
