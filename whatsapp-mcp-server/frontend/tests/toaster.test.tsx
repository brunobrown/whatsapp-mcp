import { act, render, screen, waitFor } from "@testing-library/react";
import { renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { toast } from "sonner";
import { Toaster, useNotify } from "../src/components/feedback/toaster";
import { resetNoticeMemory } from "../src/lib/notices";
import { useUiStore } from "../src/stores/ui";

function stubNotification(permission: NotificationPermission, grant: NotificationPermission = permission) {
  const created: { title: string; options?: NotificationOptions }[] = [];
  const requestPermission = vi.fn(() => Promise.resolve(grant));
  // eslint-disable-next-line @typescript-eslint/no-extraneous-class -- stands in for the browser's Notification
  class FakeNotification {
    static permission = permission;
    // Like a browser, remember the answer once the operator has given it.
    static requestPermission = vi.fn(async () => {
      FakeNotification.permission = await requestPermission();
      return FakeNotification.permission;
    });
    constructor(title: string, options?: NotificationOptions) {
      created.push({ title, ...(options && { options }) });
    }
  }
  vi.stubGlobal("Notification", FakeNotification);
  return { created, requestPermission: FakeNotification.requestPermission };
}

function setHidden(hidden: boolean) {
  vi.spyOn(document, "hidden", "get").mockReturnValue(hidden);
}

beforeEach(() => {
  resetNoticeMemory();
  useUiStore.setState(useUiStore.getInitialState(), true);
  vi.stubGlobal("matchMedia", (query: string) => ({
    matches: false,
    media: query,
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
    addListener: () => undefined,
    removeListener: () => undefined,
    onchange: null,
    dispatchEvent: () => false,
  }));
});

afterEach(() => {
  act(() => {
    toast.dismiss();
  });
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

function setup() {
  render(<Toaster />);
  return renderHook(() => useNotify()).result.current;
}

describe("toasts", () => {
  it("shows a global notice once and skips a repeat", async () => {
    stubNotification("denied");
    const { notify } = setup();
    act(() => {
      notify("refused");
      notify("refused");
    });
    expect(await screen.findAllByText("Request refused. Reload the page.")).toHaveLength(1);
    act(() => {
      notify("refused");
    });
    expect(screen.getAllByText("Request refused. Reload the page.")).toHaveLength(1);
  });

  it("shows different notices side by side, with the catalog text", async () => {
    stubNotification("denied");
    const { notify } = setup();
    act(() => {
      notify("saved");
      notify("sessionWarning", { minutes: 2 });
    });
    expect(await screen.findByText("Changes saved.")).toBeInTheDocument();
    expect(await screen.findByText("Your session ends in 2 min because of inactivity.")).toBeInTheDocument();
  });

  it("names the notification region from the catalog", async () => {
    stubNotification("denied");
    const { notify } = setup();
    act(() => {
      notify("saved");
    });
    await screen.findByText("Changes saved.");
    expect(screen.getByRole("region", { name: "Notifications" })).toBeInTheDocument();
  });

  it("drains the notice queue once a notice is delivered", async () => {
    stubNotification("denied");
    const { notify } = setup();
    act(() => {
      notify("saved");
    });
    await screen.findByText("Changes saved.");
    expect(useUiStore.getState().notices).toEqual([]);
  });
});

describe("native notifications", () => {
  it("never asks for permission on load or when a notice is raised", async () => {
    const { requestPermission } = stubNotification("default");
    const { notify } = setup();
    act(() => {
      notify("saved");
    });
    await screen.findByText("Changes saved.");
    expect(requestPermission).not.toHaveBeenCalled();
  });

  it("asks once, when the operator saves for the first time", async () => {
    const { requestPermission } = stubNotification("default", "granted");
    const { requestPermission: ask } = setup();
    await act(async () => {
      await ask();
      await ask();
    });
    expect(requestPermission).toHaveBeenCalledTimes(1);
  });

  it("does not ask again once the browser has decided", async () => {
    const { requestPermission } = stubNotification("denied");
    const { requestPermission: ask } = setup();
    await act(async () => {
      await ask();
    });
    expect(requestPermission).not.toHaveBeenCalled();
  });

  it("falls back to the toast when permission is refused", async () => {
    const { created } = stubNotification("denied");
    setHidden(true);
    const { notify } = setup();
    act(() => {
      notify("sessionEnded");
    });
    expect(await screen.findByText("Your session ended because of inactivity.")).toBeInTheDocument();
    expect(created).toEqual([]);
  });

  it("falls back to the toast when the browser has no Notification API", async () => {
    vi.stubGlobal("Notification", undefined);
    setHidden(true);
    const { notify, requestPermission } = setup();
    await act(async () => {
      await requestPermission();
    });
    act(() => {
      notify("saved");
    });
    expect(await screen.findByText("Changes saved.")).toBeInTheDocument();
  });

  it("uses a native notification when permission is granted and the tab is in the background", async () => {
    const { created } = stubNotification("granted");
    setHidden(true);
    const { notify } = setup();
    act(() => {
      notify("sessionWarning", { minutes: 2 });
    });
    await waitFor(() => {
      expect(created).toHaveLength(1);
    });
    expect(created[0]?.options?.body).toBe("Your session ends in 2 min because of inactivity.");
    expect(screen.queryByText("Your session ends in 2 min because of inactivity.")).not.toBeInTheDocument();
  });

  it("keeps the toast while the operator is looking at the tab, even with permission granted", async () => {
    const { created } = stubNotification("granted");
    setHidden(false);
    const { notify } = setup();
    act(() => {
      notify("saved");
    });
    expect(await screen.findByText("Changes saved.")).toBeInTheDocument();
    expect(created).toEqual([]);
  });

  it("falls back to the toast when the browser refuses to build the notification", async () => {
    stubNotification("granted");
    // eslint-disable-next-line @typescript-eslint/no-extraneous-class -- stands in for a browser that refuses
    class Broken {
      static permission: NotificationPermission = "granted";
      static requestPermission = vi.fn();
      constructor() {
        throw new TypeError("Illegal constructor");
      }
    }
    vi.stubGlobal("Notification", Broken);
    setHidden(true);
    const { notify } = setup();
    act(() => {
      notify("saved");
    });
    expect(await screen.findByText("Changes saved.")).toBeInTheDocument();
  });
});
