import { useSyncExternalStore } from "react";

/** Width from which the shell shows the section list and the detail side by side. */
export const WIDE_QUERY = "(min-width: 600px)";

/** Follows a CSS media query; false where `matchMedia` does not exist. */
export function useMediaQuery(query: string): boolean {
  return useSyncExternalStore(
    (notify) => {
      if (typeof window.matchMedia !== "function") return () => undefined;
      const list = window.matchMedia(query);
      list.addEventListener("change", notify);
      return () => {
        list.removeEventListener("change", notify);
      };
    },
    () => typeof window.matchMedia === "function" && window.matchMedia(query).matches,
    () => false,
  );
}
