import { useUiStore } from "@/stores/ui";
import type { Theme } from "@/stores/ui";

/** Forces light or dark through `data-theme` on <html>; "system" removes it so prefers-color-scheme decides. */
export function applyTheme(theme: Theme): void {
  const root = document.documentElement;
  if (theme === "system") root.removeAttribute("data-theme");
  else root.dataset["theme"] = theme;
}

/** Applies the stored theme now and on every change. Returns the function that stops following the store. */
export function startThemeSync(): () => void {
  applyTheme(useUiStore.getState().theme);
  return useUiStore.subscribe((state, previous) => {
    if (state.theme !== previous.theme) applyTheme(state.theme);
  });
}
