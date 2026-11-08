import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { filterEntries, menuGroups } from "../src/features/menu/model";
import { resolveMenu, THEME_STORAGE_KEY, useUiStore } from "../src/stores/ui";
import type { SectionSummary } from "../src/types/api";

function entry(id: string, title = id, description = ""): SectionSummary {
  return { id, title, icon: "shield", order: 0, kind: "section", description, restart: null, pending: false };
}

beforeEach(() => {
  localStorage.clear();
  useUiStore.setState(useUiStore.getInitialState(), true);
});
afterEach(() => {
  vi.restoreAllMocks();
});

describe("menu model", () => {
  it("UXP-05 groups the sections under Overview, Messaging, Safety and data, Preferences and More, in that order", () => {
    const ids = ["account", "media", "status", "send_policy", "unknown_thing", "interface", "activity", "webhooks", "privacy"];
    const groups = menuGroups(ids.map((id) => entry(id)));
    expect(groups.map((g) => [g.titleKey, g.entries.map((e) => e.id)])).toEqual([
      ["menu.group.overview", ["status", "activity"]],
      ["menu.group.messaging", ["send_policy", "webhooks"]],
      ["menu.group.safety", ["media", "privacy"]],
      ["menu.group.preferences", ["account", "interface"]],
      ["menu.group.more", ["unknown_thing"]],
    ]);
  });

  it("UXP-05 groups omit a heading that has no entry, and More only exists for sections outside the map", () => {
    const groups = menuGroups([entry("status"), entry("account")]);
    expect(groups.map((g) => g.id)).toEqual(["overview", "preferences"]);
    expect(menuGroups([])).toEqual([]);
  });

  it("UXP-06 filter keeps entries whose title or description contains the text, ignoring case", () => {
    const list = [entry("a", "Send policy", "Who may send"), entry("b", "Media downloads", "Where files go"), entry("c", "Account", "Password")];
    expect(filterEntries(list, "SEND").map((e) => e.id)).toEqual(["a"]);
    expect(filterEntries(list, "files").map((e) => e.id)).toEqual(["b"]);
    expect(filterEntries(list, "  ").map((e) => e.id)).toEqual(["a", "b", "c"]);
    expect(filterEntries(list, "zzz")).toEqual([]);
  });
});

describe("menu state", () => {
  it("UXP-03 resolves by width: drawer under 600, rail from 600 to 1023, expanded from 1024, and an explicit choice wins from 600", () => {
    expect([360, 599].map((w) => resolveMenu(w, "auto"))).toEqual(["drawer", "drawer"]);
    expect([600, 1023].map((w) => resolveMenu(w, "auto"))).toEqual(["collapsed", "collapsed"]);
    expect([1024, 1920].map((w) => resolveMenu(w, "auto"))).toEqual(["expanded", "expanded"]);
    expect(resolveMenu(700, "expanded")).toBe("expanded");
    expect(resolveMenu(1400, "collapsed")).toBe("collapsed");
    expect(resolveMenu(400, "expanded")).toBe("drawer");
  });

  it("UXP-03 the state is stored and a blocked storage uses the default", async () => {
    useUiStore.getState().setMenu("collapsed");
    const saved = localStorage.getItem(THEME_STORAGE_KEY);
    expect(saved).toContain('"menu":"collapsed"');

    useUiStore.setState({ menu: "auto" });
    localStorage.setItem(THEME_STORAGE_KEY, saved ?? "");
    await useUiStore.persist.rehydrate();
    expect(useUiStore.getState().menu).toBe("collapsed");

    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new DOMException("blocked", "SecurityError");
    });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("blocked", "SecurityError");
    });
    useUiStore.setState({ menu: "auto" });
    await useUiStore.persist.rehydrate();
    expect(useUiStore.getState().menu).toBe("auto");
    useUiStore.getState().setMenu("expanded");
    expect(useUiStore.getState().menu).toBe("expanded");
  });

  it("UXP-03 a stored value that is not a choice falls back to auto", async () => {
    localStorage.setItem(THEME_STORAGE_KEY, '{"state":{"theme":"dark","menu":"wide"},"version":1}');
    await useUiStore.persist.rehydrate();
    expect(useUiStore.getState().menu).toBe("auto");
    expect(useUiStore.getState().theme).toBe("dark");
  });
});
