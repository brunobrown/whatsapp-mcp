import { useSyncExternalStore } from "react";
import { t } from "@/i18n/t";
import { relativeText } from "@/lib/relativeTime";

const listeners = new Set<() => void>();
let timer: ReturnType<typeof setInterval> | undefined;
let tick = 0;

const wholeSecond = (): number => Math.floor(Date.now() / 1000) * 1000;

function subscribe(listener: () => void): () => void {
  if (listeners.size === 0) {
    tick = wholeSecond();
    timer = setInterval(() => {
      tick = wholeSecond();
      listeners.forEach((notify) => {
        notify();
      });
    }, 1000);
  }
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
    if (listeners.size === 0 && timer !== undefined) {
      clearInterval(timer);
      timer = undefined;
    }
  };
}

/** The current time in epoch milliseconds, renewed once a second by one shared timer. */
export function useNow(): number {
  return useSyncExternalStore(subscribe, () => (listeners.size > 0 ? tick : wholeSecond()), wholeSecond);
}

/** The part of a React Query result that freshness reads. */
export interface FreshnessSource {
  dataUpdatedAt: number;
  isRefetchError: boolean;
  isFetching: boolean;
  refetch: () => Promise<unknown>;
}

export interface Freshness {
  /** 'Updated 5 s ago'; empty until the first successful fetch. */
  label: string;
  /** Epoch milliseconds of the last good data; 0 before the first. */
  updatedAt: number;
  /** The last fetch failed: the data on screen is the previous one. */
  failed: boolean;
  refreshing: boolean;
  /** Fetches again at once. */
  refresh: () => void;
}

/**
 * 'Updated N s ago' for a query, counting every second, with a refresh and a failure flag. Given several queries it
 * reads the oldest good data, refreshes them all and reports a failure of any.
 */
export function useFreshness(source: FreshnessSource | readonly FreshnessSource[]): Freshness {
  const now = useNow();
  const queries = Array.isArray(source) ? (source as readonly FreshnessSource[]) : [source as FreshnessSource];
  const refresh = (): void => {
    for (const query of queries) void query.refetch();
  };
  const good = queries.map((query) => query.dataUpdatedAt).filter((moment) => moment > 0);
  const updatedAt = good.length > 0 ? Math.min(...good) : 0;
  return {
    label: updatedAt > 0 ? t("freshness.updated", { when: relativeText(now, updatedAt) }) : "",
    updatedAt,
    failed: queries.some((query) => query.isRefetchError),
    refreshing: queries.some((query) => query.isFetching),
    refresh,
  };
}
