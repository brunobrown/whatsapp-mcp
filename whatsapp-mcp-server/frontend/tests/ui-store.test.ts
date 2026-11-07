import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { THEME_STORAGE_KEY, useUiStore } from "../src/stores/ui";

beforeEach(() => {
  localStorage.clear();
  useUiStore.setState(useUiStore.getInitialState(), true);
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("ui store: theme", () => {
  it("follows the system until the operator forces a theme", () => {
    expect(useUiStore.getState().theme).toBe("system");
  });

  it("remembers a forced theme and reads it back after a reload", async () => {
    useUiStore.getState().setTheme("dark");
    const saved = localStorage.getItem(THEME_STORAGE_KEY);
    expect(saved).toContain('"dark"');

    // A reload: memory starts from the default again while the browser still holds what was saved.
    useUiStore.setState({ theme: "system" });
    localStorage.setItem(THEME_STORAGE_KEY, saved ?? "");
    await useUiStore.persist.rehydrate();
    expect(useUiStore.getState().theme).toBe("dark");
  });

  it("stores nothing but the theme", () => {
    useUiStore.getState().setTheme("light");
    useUiStore.getState().addRestart(["bridge"]);
    useUiStore.getState().setMobilePane("detail");
    const stored = JSON.parse(localStorage.getItem(THEME_STORAGE_KEY) ?? "{}") as { state: Record<string, unknown> };
    expect(Object.keys(stored.state)).toEqual(["theme"]);
  });

  it("keeps working when localStorage refuses writes", () => {
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("blocked", "SecurityError");
    });
    expect(() => {
      useUiStore.getState().setTheme("light");
    }).not.toThrow();
    expect(useUiStore.getState().theme).toBe("light");
  });

  it("keeps working when localStorage refuses reads", async () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new DOMException("blocked", "SecurityError");
    });
    await expect(useUiStore.persist.rehydrate()).resolves.not.toThrow();
    expect(useUiStore.getState().theme).toBe("system");
  });

  it("falls back to the system theme when the stored value is garbage", async () => {
    localStorage.setItem(THEME_STORAGE_KEY, '{"state":{"theme":"neon"},"version":1}');
    await useUiStore.persist.rehydrate();
    expect(useUiStore.getState().theme).toBe("system");
  });
});

describe("ui store: mobile pane", () => {
  it("starts on the list and switches to the detail and back", () => {
    expect(useUiStore.getState().mobilePane).toBe("list");
    useUiStore.getState().setMobilePane("detail");
    expect(useUiStore.getState().mobilePane).toBe("detail");
    useUiStore.getState().setMobilePane("list");
    expect(useUiStore.getState().mobilePane).toBe("list");
  });
});

describe("ui store: restart scopes", () => {
  it("deduplicates scopes across calls and inside one call", () => {
    const { addRestart } = useUiStore.getState();
    addRestart(["bridge", "bridge"]);
    addRestart(["server", "bridge"]);
    expect(useUiStore.getState().restart).toEqual(["bridge", "server"]);
  });

  it("forgets the scopes, the marked sections and the unsaved-edit flag when the session ends", () => {
    const { addRestart, setDraftDirty, resetSession, setTheme } = useUiStore.getState();
    addRestart(["bridge", "server"], "media");
    setDraftDirty(true);
    setTheme("dark");
    resetSession();
    const after = useUiStore.getState();
    expect(after.restart).toEqual([]);
    expect(after.restartSections).toEqual([]);
    expect(after.draftDirty).toBe(false);
    expect(after.theme).toBe("dark");
  });
});

describe("ui store: notices", () => {
  it("keeps only the last 5, oldest dropped first", () => {
    const { pushNotice } = useUiStore.getState();
    for (let n = 1; n <= 7; n += 1) pushNotice({ key: `k${String(n)}`, text: `Notice ${String(n)}`, at: n });
    const kept = useUiStore.getState().notices;
    expect(kept.map((notice) => notice.key)).toEqual(["k3", "k4", "k5", "k6", "k7"]);
  });

  it("gives each notice its own id and lets one be dismissed", () => {
    const { pushNotice } = useUiStore.getState();
    pushNotice({ key: "a", text: "A", at: 1 });
    pushNotice({ key: "b", text: "B", at: 2 });
    const [first, second] = useUiStore.getState().notices;
    expect(first?.id).not.toBe(second?.id);
    useUiStore.getState().dismissNotice(first?.id ?? -1);
    expect(useUiStore.getState().notices.map((notice) => notice.key)).toEqual(["b"]);
  });
});
