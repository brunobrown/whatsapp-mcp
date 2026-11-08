import { create } from "zustand";
import { createJSONStorage, persist } from "zustand/middleware";
import type { StateStorage } from "zustand/middleware";
import type { RestartScope } from "@/types/api";

export type Theme = "system" | "light" | "dark";
/** The operator's choice for the side menu; "auto" follows the width. */
export type MenuPreference = "auto" | "expanded" | "collapsed";
/** What the menu is at a given width: a drawer on phones, otherwise expanded or a rail. */
export type MenuMode = "drawer" | "expanded" | "collapsed";

/** Width from which the side menu is shown beside the content instead of in a drawer. */
export const MENU_BESIDE_FROM = 600;
/** Width from which "auto" shows the menu expanded. */
export const MENU_EXPANDED_FROM = 1024;

export interface Notice {
  id: number;
  /** Identifies the kind of notice, so a repeat can be recognised. */
  key: string;
  text: string;
  /** Milliseconds since the epoch when the notice was raised. */
  at: number;
}

export type NewNotice = Omit<Notice, "id">;

export const THEME_STORAGE_KEY = "wmcp.ui";
export const MAX_NOTICES = 5;

const THEMES: readonly Theme[] = ["system", "light", "dark"];
const MENUS: readonly MenuPreference[] = ["auto", "expanded", "collapsed"];

/** The menu a width calls for: under 600 px always the drawer; above, the stored choice or, for "auto", the width default. */
export function resolveMenu(width: number, menu: MenuPreference): MenuMode {
  if (width < MENU_BESIDE_FROM) return "drawer";
  if (menu === "auto") return width >= MENU_EXPANDED_FROM ? "expanded" : "collapsed";
  return menu;
}

/** localStorage can be blocked or full; the theme then lives for the session only. */
const safeStorage: StateStorage = {
  getItem: (name) => {
    try {
      return localStorage.getItem(name);
    } catch {
      return null;
    }
  },
  setItem: (name, value) => {
    try {
      localStorage.setItem(name, value);
    } catch {
      /* keep the choice in memory only */
    }
  },
  removeItem: (name) => {
    try {
      localStorage.removeItem(name);
    } catch {
      /* nothing stored, nothing to remove */
    }
  },
};

interface UiState {
  theme: Theme;
  menu: MenuPreference;
  restart: RestartScope[];
  /** Sections whose saved value waits for a restart; the marker belongs to them, not to every section of the scope. */
  restartSections: string[];
  /** Whether the open section holds edits that were not saved; signing out asks before dropping them. */
  draftDirty: boolean;
  setDraftDirty: (dirty: boolean) => void;
  notices: Notice[];
  setTheme: (theme: Theme) => void;
  setMenu: (menu: MenuPreference) => void;
  addRestart: (scopes: readonly RestartScope[], sectionId?: string) => void;
  /** Forgets everything that belonged to the session that ended: restart markers and unsaved-edit state. */
  resetSession: () => void;
  pushNotice: (notice: NewNotice) => void;
  dismissNotice: (id: number) => void;
}

let nextNoticeId = 1;

function isTheme(value: unknown): value is Theme {
  return THEMES.some((theme) => theme === value);
}

function isMenu(value: unknown): value is MenuPreference {
  return MENUS.some((menu) => menu === value);
}

/** Local interface state only. Remote data belongs to TanStack Query. */
export const useUiStore = create<UiState>()(
  persist(
    (set) => ({
      theme: "system",
      menu: "auto",
      restart: [],
      restartSections: [],
      draftDirty: false,
      setDraftDirty: (draftDirty) => {
        set({ draftDirty });
      },
      notices: [],
      setTheme: (theme) => {
        set({ theme });
      },
      setMenu: (menu) => {
        set({ menu });
      },
      addRestart: (scopes, sectionId) => {
        set((state) => ({
          restart: [...new Set([...state.restart, ...scopes])],
          restartSections: sectionId === undefined ? state.restartSections : [...new Set([...state.restartSections, sectionId])],
        }));
      },
      resetSession: () => {
        set({ restart: [], restartSections: [], draftDirty: false });
      },
      pushNotice: (notice) => {
        set((state) => ({
          notices: [...state.notices, { ...notice, id: nextNoticeId++ }].slice(-MAX_NOTICES),
        }));
      },
      dismissNotice: (id) => {
        set((state) => ({ notices: state.notices.filter((notice) => notice.id !== id) }));
      },
    }),
    {
      name: THEME_STORAGE_KEY,
      version: 1,
      storage: createJSONStorage(() => safeStorage),
      partialize: (state) => ({ theme: state.theme, menu: state.menu }),
      merge: (persisted, current) => {
        const stored = persisted as { theme?: unknown; menu?: unknown } | null;
        return {
          ...current,
          theme: isTheme(stored?.theme) ? stored.theme : current.theme,
          menu: isMenu(stored?.menu) ? stored.menu : current.menu,
        };
      },
    },
  ),
);
