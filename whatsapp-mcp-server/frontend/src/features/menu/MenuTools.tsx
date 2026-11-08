import { LogOut, Monitor, Moon, Sun } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { useNavigate } from "@tanstack/react-router";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/dialog";
import { Tooltip, TooltipProvider } from "@/components/ui/tooltip";
import { useT } from "@/i18n/t";
import { sessionService } from "@/services/services";
import { useUiStore } from "@/stores/ui";
import type { Theme } from "@/stores/ui";

const THEME_ORDER: readonly Theme[] = ["system", "light", "dark"];
const THEME_ICONS: Record<Theme, LucideIcon> = {
  system: Monitor,
  light: Sun,
  dark: Moon,
};

function ThemeButton({ tooltip }: { tooltip: boolean }) {
  const t = useT();
  const theme = useUiStore((state) => state.theme);
  const setTheme = useUiStore((state) => state.setTheme);
  const Icon = THEME_ICONS[theme];
  const next = THEME_ORDER[(THEME_ORDER.indexOf(theme) + 1) % THEME_ORDER.length] ?? "system";
  // The button shows the theme in use and is named by the one a press switches to; the tooltip says the same.
  const label = t("shell.theme", { name: t(`shell.theme.${next}`) });
  const button = (
    <Button
      variant="ghost"
      size="icon"
      aria-label={label}
      onClick={() => {
        setTheme(next);
      }}
    >
      <Icon aria-hidden="true" />
    </Button>
  );
  return tooltip ? (
    <Tooltip label={label} side="bottom">
      {button}
    </Tooltip>
  ) : (
    button
  );
}

function SignOutButton() {
  const t = useT();
  const navigate = useNavigate();
  const [asking, setAsking] = useState(false);
  const signOut = () => {
    void sessionService
      .logout()
      .catch(() => undefined)
      .then(() => navigate({ to: "/login", replace: true }));
  };
  return (
    <>
      <Button
        variant="ghost"
        size="icon"
        aria-label={t("shell.signOut")}
        onClick={() => {
          if (useUiStore.getState().draftDirty) setAsking(true);
          else signOut();
        }}
      >
        <LogOut aria-hidden="true" />
      </Button>
      <ConfirmDialog
        open={asking}
        onOpenChange={setAsking}
        title={t("guard.signOut.title")}
        description={t("guard.signOut.description")}
        destructive
        consequence={t("guard.consequence")}
        confirmLabel={t("guard.signOut.confirm")}
        cancelLabel={t("guard.stay")}
        onConfirm={signOut}
      />
    </>
  );
}

/**
 * The theme and sign-out buttons: in the menu header, in the rail footer and in the drawer header. The drawer sets
 * `tooltip` to false: focus lands on its first button when it opens, and a tooltip opened by that focus would take
 * the first Escape that should close the drawer.
 */
export function MenuTools({ tooltip = true }: { tooltip?: boolean }) {
  return (
    <TooltipProvider>
      <ThemeButton tooltip={tooltip} />
      <SignOutButton />
    </TooltipProvider>
  );
}

