import { QueryClient, QueryClientProvider, useQuery } from "@tanstack/react-query";
import { act, render, renderHook, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { RelativeTime } from "../src/components/feedback/RelativeTime";
import { relativeText } from "../src/lib/relativeTime";
import { useFreshness } from "../src/lib/useFreshness";

const T0 = Date.parse("2026-11-07T12:00:00Z");

beforeEach(() => {
  vi.useFakeTimers({ now: T0, toFake: ["Date", "setInterval", "clearInterval", "setTimeout", "clearTimeout"] });
});
afterEach(() => {
  vi.useRealTimers();
});

describe("relativeText", () => {
  it("UXP-26 counts seconds then minutes", () => {
    const at = (secondsAgo: number): string => relativeText(T0, T0 - secondsAgo * 1000);
    expect(at(0)).toBe("0 s ago");
    expect(at(1)).toBe("1 s ago");
    expect(at(59)).toBe("59 s ago");
    expect(at(60)).toBe("1 min ago");
    expect(at(119)).toBe("1 min ago");
    expect(at(120)).toBe("2 min ago");
    expect(at(3599)).toBe("59 min ago");
  });

  it("UXP-30 goes on to hours and days, and a moment in the future reads 0 s", () => {
    expect(relativeText(T0, T0 - 3600 * 1000)).toBe("1 h ago");
    expect(relativeText(T0, T0 - 2 * 3600 * 1000 - 5000)).toBe("2 h ago");
    expect(relativeText(T0, T0 - 86_400 * 1000)).toBe("1 d ago");
    expect(relativeText(T0, T0 - 3 * 86_400 * 1000)).toBe("3 d ago");
    expect(relativeText(T0, T0 + 5000)).toBe("0 s ago");
  });
});

describe("RelativeTime", () => {
  it("UXP-30 shows relative text with the UTC time in title and datetime", () => {
    render(<RelativeTime value="2026-11-07T10:00:00Z" />);
    const element = screen.getByText("2 h ago");
    expect(element.tagName).toBe("TIME");
    expect(element).toHaveAttribute("datetime", "2026-11-07T10:00:00.000Z");
    expect(element).toHaveAttribute("title", "2026-11-07 10:00:00 UTC");
  });

  it("UXP-30 renders nothing for a value that is not a moment", () => {
    const { container } = render(<RelativeTime value="not a date" />);
    expect(container).toBeEmptyDOMElement();
  });

  it("UXP-26 counts up every second", () => {
    render(<RelativeTime value={T0} />);
    expect(screen.getByText("0 s ago")).toBeInTheDocument();
    act(() => {
      vi.advanceTimersByTime(3000);
    });
    expect(screen.getByText("3 s ago")).toBeInTheDocument();
    act(() => {
      vi.advanceTimersByTime(57_000);
    });
    expect(screen.getByText("1 min ago")).toBeInTheDocument();
  });
});

function setup(fetcher: () => Promise<{ n: number }>) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
  const wrapper = ({ children }: { children: ReactNode }) => <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  return renderHook(
    () => {
      const query = useQuery({ queryKey: ["fresh"], queryFn: fetcher });
      return useFreshness(query);
    },
    { wrapper },
  );
}

describe("useFreshness", () => {
  it("UXP-26 reads 'Updated N s ago' and counts every second", async () => {
    const { result } = setup(() => Promise.resolve({ n: 1 }));
    expect(result.current.label).toBe("");
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(result.current.label).toBe("Updated 0 s ago");
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });
    expect(result.current.label).toBe("Updated 5 s ago");
    await act(async () => {
      await vi.advanceTimersByTimeAsync(60_000);
    });
    expect(result.current.label).toBe("Updated 1 min ago");
  });

  it("UXP-27 refresh fetches again and resets to 0 s", async () => {
    const fetcher = vi.fn(() => Promise.resolve({ n: 1 }));
    const { result } = setup(fetcher);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(30_000);
    });
    expect(result.current.label).toBe("Updated 30 s ago");
    act(() => {
      result.current.refresh();
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(fetcher).toHaveBeenCalledTimes(2);
    expect(result.current.label).toBe("Updated 0 s ago");
    expect(result.current.failed).toBe(false);
  });

  it("UXP-28 a failed update keeps the last good time", async () => {
    let fail = false;
    const { result } = setup(() => (fail ? Promise.reject(new Error("down")) : Promise.resolve({ n: 1 })));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(20_000);
    });
    fail = true;
    act(() => {
      result.current.refresh();
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(result.current.failed).toBe(true);
    expect(result.current.label).toBe("Updated 20 s ago");
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000);
    });
    expect(result.current.label).toBe("Updated 23 s ago");
  });
});
