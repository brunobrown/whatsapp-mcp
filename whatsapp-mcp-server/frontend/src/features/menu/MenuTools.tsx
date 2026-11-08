import { LogOut, Monitor, Moon, Sun } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { useNavigate } from "@tanstack/react-router";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/dialog";
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

function ThemeButton() {
  const t = useT();
  const theme = useUiStore((state) => state.theme);
  const setTheme = useUiStore((state) => state.setTheme);
  const Icon = THEME_ICONS[theme];
  const next = THEME_ORDER[(THEME_ORDER.indexOf(theme) + 1) % THEME_ORDER.length] ?? "system";
  return (
    <Button
      variant="ghost"
      size="icon"
      aria-label={t("shell.theme", { name: t(`shell.theme.${theme}`) })}
      onClick={() => {
        setTheme(next);
      }}
    >
      <Icon aria-hidden="true" />
    </Button>
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

/** The theme and sign-out buttons: in the menu header, in the rail footer and in the drawer header. */
export function MenuTools() {
  return (
    <>
      <ThemeButton />
      <SignOutButton />
    </>
  );
}

