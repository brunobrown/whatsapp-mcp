import { create } from "zustand";
import { createJSONStorage, persist } from "zustand/middleware";
import type { StateStorage } from "zustand/middleware";
import type { RestartScope } from "@/types/api";

export type Theme = "system" | "light" | "dark";
export type MobilePane = "list" | "detail";

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
  mobilePane: MobilePane;
  restart: RestartScope[];
  /** Sections whose saved value waits for a restart; the marker belongs to them, not to every section of the scope. */
  restartSections: string[];
  /** Whether the open section holds edits that were not saved; signing out asks before dropping them. */
  draftDirty: boolean;
  setDraftDirty: (dirty: boolean) => void;
  notices: Notice[];
  setTheme: (theme: Theme) => void;
  setMobilePane: (pane: MobilePane) => void;
  addRestart: (scopes: readonly RestartScope[], sectionId?: string) => void;
  clearRestart: (scope?: RestartScope) => void;
  pushNotice: (notice: NewNotice) => void;
  dismissNotice: (id: number) => void;
}

let nextNoticeId = 1;

function isTheme(value: unknown): value is Theme {
  return THEMES.some((theme) => theme === value);
}

/** Local interface state only. Remote data belongs to TanStack Query. */
export const useUiStore = create<UiState>()(
  persist(
    (set) => ({
      theme: "system",
      mobilePane: "list",
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
      setMobilePane: (mobilePane) => {
        set({ mobilePane });
      },
      addRestart: (scopes, sectionId) => {
        set((state) => ({
          restart: [...new Set([...state.restart, ...scopes])],
          restartSections: sectionId === undefined ? state.restartSections : [...new Set([...state.restartSections, sectionId])],
        }));
      },
      clearRestart: (scope) => {
        set((state) => ({
          restart: scope === undefined ? [] : state.restart.filter((s) => s !== scope),
          restartSections: scope === undefined ? [] : state.restartSections,
        }));
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
      partialize: (state) => ({ theme: state.theme }),
      merge: (persisted, current) => {
        const theme = (persisted as { theme?: unknown } | null)?.theme;
        return { ...current, theme: isTheme(theme) ? theme : current.theme };
      },
    },
  ),
);
