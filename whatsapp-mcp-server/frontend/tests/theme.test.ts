import { beforeEach, describe, expect, it } from "vitest";
import { applyTheme, startThemeSync } from "../src/lib/theme";
import { useUiStore } from "../src/stores/ui";

describe("theme class", () => {
  beforeEach(() => {
    document.documentElement.removeAttribute("data-theme");
  });

  it("leaves the system theme alone and forces light or dark when asked", () => {
    applyTheme("dark");
    expect(document.documentElement.dataset["theme"]).toBe("dark");
    applyTheme("light");
    expect(document.documentElement.dataset["theme"]).toBe("light");
    applyTheme("system");
    expect(document.documentElement.hasAttribute("data-theme")).toBe(false);
  });

  it("applies the store theme now and whenever the operator changes it", () => {
    useUiStore.setState({ theme: "dark" });
    const stop = startThemeSync();
    expect(document.documentElement.dataset["theme"]).toBe("dark");
    useUiStore.getState().setTheme("system");
    expect(document.documentElement.hasAttribute("data-theme")).toBe(false);
    stop();
    useUiStore.getState().setTheme("light");
    expect(document.documentElement.hasAttribute("data-theme")).toBe(false);
    useUiStore.getState().setTheme("system");
  });
});
