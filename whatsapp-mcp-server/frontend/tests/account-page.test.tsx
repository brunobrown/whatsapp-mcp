import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryHistory, RouterProvider } from "@tanstack/react-router";
import { http, HttpResponse } from "msw";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppProviders } from "../src/app/providers";
import { createAppRouter } from "../src/app/router";
import { queryClient, SESSION_KEY } from "../src/lib/queryClient";
import { useUiStore } from "../src/stores/ui";
import { mockViewport } from "./helpers/viewport";
import { server } from "./msw/server";

const sections = [
  { id: "account", title: "Account", icon: "user", order: 100, kind: "page", description: "", restart: null },
];

let viewport: ReturnType<typeof mockViewport>;

function openAccount() {
  const router = createAppRouter(createMemoryHistory({ initialEntries: ["/account"] }));
  render(
    <AppProviders router={router}>
      <RouterProvider router={router} />
    </AppProviders>,
  );
  return router;
}

async function fill(user: ReturnType<typeof userEvent.setup>, current: string, fresh: string, confirm: string) {
  await user.type(await screen.findByLabelText("Current password"), current);
  await user.type(screen.getByLabelText("New password"), fresh);
  await user.type(screen.getByLabelText("Repeat the new password"), confirm);
}

beforeEach(() => {
  viewport = mockViewport(1280);
  queryClient.clear();
  useUiStore.setState(useUiStore.getInitialState(), true);
  server.use(
    http.get("*/api/session", () => HttpResponse.json({ state: "authenticated", csrf: "c", idle_remaining: 900 })),
    http.get("*/api/sections", () => HttpResponse.json(sections)),
  );
});
afterEach(() => {
  viewport.restore();
  vi.useRealTimers();
});

describe("account page: change password", () => {
  it("sends the current, new and repeated passwords, confirms with the server's words and empties the form", async () => {
    let body: unknown = null;
    server.use(
      http.post("*/api/account/password", async ({ request }) => {
        body = await request.json();
        return HttpResponse.json({ ok: true, message: "Password changed." });
      }),
    );
    const user = userEvent.setup();
    openAccount();
    await fill(user, "old password 1", "brand new password", "brand new password");
    await user.click(screen.getByRole("button", { name: "Change password" }));
    const main = document.getElementById("content") as HTMLElement;
    expect(await within(main).findByText("Password changed.")).toBeInTheDocument();
    expect(body).toEqual({ current: "old password 1", new: "brand new password", confirm: "brand new password" });
    expect(screen.getByLabelText("Current password")).toHaveValue("");
    expect(screen.getByLabelText("New password")).toHaveValue("");
    expect(screen.getByLabelText("Repeat the new password")).toHaveValue("");
  });

  it.each([
    ["a mismatch", "The two passwords do not match."],
    ["a weak password", "The password must have at least 12 characters."],
    ["a wrong current password", "The current password is not right."],
  ])("shows the server message for %s and puts the focus back on the first field", async (_name, message) => {
    server.use(http.post("*/api/account/password", () => HttpResponse.json({ ok: false, message })));
    const user = userEvent.setup();
    openAccount();
    await fill(user, "x", "y", "z");
    await user.click(screen.getByRole("button", { name: "Change password" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(message);
    expect(screen.getByLabelText("Current password")).toHaveFocus();
    expect(screen.getByLabelText("Current password")).toHaveAttribute("aria-invalid", "true");
    expect(screen.getByLabelText("New password")).toHaveValue("");
  });

  it("respects the throttle wait: the button is disabled and counts down, then works again", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    server.use(
      http.post("*/api/account/password", () =>
        HttpResponse.json({ ok: false, message: "Too many attempts. Wait 3 seconds.", wait: 3 }, { status: 429 }),
      ),
    );
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    openAccount();
    await fill(user, "x", "y", "z");
    await user.click(screen.getByRole("button", { name: "Change password" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Too many attempts. Wait 3 seconds.");
    expect(screen.getByRole("button", { name: "Wait 3 s" })).toBeDisabled();
    act(() => {
      vi.advanceTimersByTime(1000);
    });
    expect(screen.getByRole("button", { name: "Wait 2 s" })).toBeDisabled();
    act(() => {
      vi.advanceTimersByTime(2000);
    });
    expect(screen.getByRole("button", { name: "Change password" })).toBeEnabled();
  });

  it("names its fields and gives them the browser's password hints", async () => {
    openAccount();
    expect(await screen.findByLabelText("Current password")).toHaveAttribute("autocomplete", "current-password");
    expect(screen.getByLabelText("New password")).toHaveAttribute("autocomplete", "new-password");
    expect(screen.getByLabelText("Repeat the new password")).toHaveAttribute("type", "password");
  });
});

describe("account page: sign out", () => {
  it("clears the cache and lands on the sign-in page", async () => {
    let deleted = false;
    server.use(
      http.delete("*/api/session", () => {
        deleted = true;
        return HttpResponse.json({ ok: true });
      }),
    );
    const router = openAccount();
    await screen.findByRole("heading", { level: 2, name: "This session" });
    expect(queryClient.getQueryData(SESSION_KEY)).toBeDefined();
    queryClient.setQueryData(["section", "send_policy"], { id: "send_policy" });
    server.use(http.get("*/api/session", () => HttpResponse.json({ state: "login", csrf: "c" })));
    await userEvent.click(screen.getByRole("button", { name: "Sign out of the interface" }));
    await waitFor(() => {
      expect(router.state.location.pathname).toBe("/login");
    });
    expect(deleted).toBe(true);
    expect(queryClient.getQueryData(["section", "send_policy"])).toBeUndefined();
    expect(await screen.findByRole("heading", { level: 1, name: "Sign in" })).toBeInTheDocument();
  });
});
