import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryHistory, RouterProvider } from "@tanstack/react-router";
import { http, HttpResponse } from "msw";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppProviders } from "../src/app/providers";
import { createAppRouter } from "../src/app/router";
import { queryClient } from "../src/lib/queryClient";
import { mockViewport } from "./helpers/viewport";
import { server } from "./msw/server";

const sections = [
  { id: "send_policy", title: "Send policy", icon: "shield", order: 10, kind: "section", description: "", restart: null },
];
let viewport: ReturnType<typeof mockViewport>;

function openSetup() {
  const router = createAppRouter(createMemoryHistory({ initialEntries: ["/login"] }));
  render(
    <AppProviders router={router}>
      <RouterProvider router={router} />
    </AppProviders>,
  );
  return router;
}

async function fill(user: ReturnType<typeof userEvent.setup>, code: string, password: string, confirm: string) {
  await user.type(await screen.findByLabelText("Setup code"), code);
  await user.type(screen.getByLabelText("New password"), password);
  await user.type(screen.getByLabelText("Repeat the password"), confirm);
}

beforeEach(() => {
  viewport = mockViewport(1280);
  queryClient.clear();
  server.use(
    http.get("*/api/session", () => HttpResponse.json({ state: "setup", csrf: "c0" })),
    http.get("*/api/sections", () => HttpResponse.json(sections)),
  );
});
afterEach(() => {
  viewport.restore();
  vi.useRealTimers();
});

describe("first-password page", () => {
  it("is shown while no password exists, with password-type fields that offer a new password", async () => {
    openSetup();
    expect(await screen.findByRole("heading", { level: 1, name: "Create the password" })).toBeInTheDocument();
    expect(screen.getByLabelText("New password")).toHaveAttribute("type", "password");
    expect(screen.getByLabelText("New password")).toHaveAttribute("autocomplete", "new-password");
    expect(screen.getByLabelText("Repeat the password")).toHaveAttribute("type", "password");
    expect(screen.getByLabelText("Repeat the password")).toHaveAttribute("autocomplete", "new-password");
    expect(screen.queryByLabelText("Password")).toBeNull();
  });

  it("states the 30-second interval between code requests", async () => {
    openSetup();
    expect(await screen.findByText("A new code can be requested every 30 seconds.")).toBeInTheDocument();
  });

  it("asks for a code, shows the server's message, and never fills the code field from the answer", async () => {
    server.use(
      http.post("*/api/session/code", () =>
        HttpResponse.json({ announced: true, message: "A setup code was printed in the bridge log.", code: "ABCD-EFGH-JKLM" }),
      ),
    );
    openSetup();
    await userEvent.click(await screen.findByRole("button", { name: "Print a setup code" }));
    expect(await screen.findByText("A setup code was printed in the bridge log.")).toBeInTheDocument();
    expect(screen.queryByText(/ABCD/)).toBeNull();
    expect(screen.getByLabelText("Setup code")).toHaveValue("");
    expect(screen.getByLabelText("Setup code")).toHaveAttribute("autocomplete", "off");
  });

  it("blocks another request for 30 seconds after a printed code, counting down", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    server.use(http.post("*/api/session/code", () => HttpResponse.json({ announced: true, message: "Printed." })));
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    openSetup();
    await user.click(await screen.findByRole("button", { name: "Print a setup code" }));
    expect(await screen.findByRole("button", { name: "Ask again in 30 s" })).toBeDisabled();
    act(() => {
      vi.advanceTimersByTime(10_000);
    });
    expect(screen.getByRole("button", { name: "Ask again in 20 s" })).toBeDisabled();
    act(() => {
      vi.advanceTimersByTime(20_000);
    });
    expect(screen.getByRole("button", { name: "Print a setup code" })).toBeEnabled();
  });

  it("shows the refusal of a code request without blocking the button", async () => {
    server.use(http.post("*/api/session/code", () => HttpResponse.json({ announced: false, message: "A setup code is not needed." })));
    openSetup();
    await userEvent.click(await screen.findByRole("button", { name: "Print a setup code" }));
    expect(await screen.findByText("A setup code is not needed.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Print a setup code" })).toBeEnabled();
  });

  it("creates the password with code, password and confirmation, then opens the first section", async () => {
    let body: unknown = null;
    server.use(
      http.post("*/api/session/setup", async ({ request }) => {
        body = await request.json();
        return HttpResponse.json({ ok: true, csrf: "c1" });
      }),
    );
    const router = openSetup();
    const user = userEvent.setup();
    await fill(user, "ABCD-EFGH-JKLM", "a long enough password", "a long enough password");
    await user.click(screen.getByRole("button", { name: "Create password" }));
    await waitFor(() => {
      expect(router.state.location.pathname).toBe("/s/send_policy");
    });
    expect(body).toEqual({ code: "ABCD-EFGH-JKLM", password: "a long enough password", confirm: "a long enough password" });
  });

  it.each([
    ["a weak password", "The password must have at least 12 characters."],
    ["mismatched passwords", "The two passwords do not match."],
  ])("shows the server's message for %s and keeps the page", async (_name, message) => {
    server.use(http.post("*/api/session/setup", () => HttpResponse.json({ ok: false, message })));
    const router = openSetup();
    const user = userEvent.setup();
    await fill(user, "CODE", "short", "short{Enter}");
    expect(await screen.findByRole("alert")).toHaveTextContent(message);
    expect(router.state.location.pathname).toBe("/login");
    expect(screen.getByLabelText("New password")).toHaveValue("");
    expect(screen.getByLabelText("Repeat the password")).toHaveValue("");
  });

  it("honours a wait on the setup attempt", async () => {
    server.use(
      http.post("*/api/session/setup", () =>
        HttpResponse.json({ ok: false, message: "Too many attempts. Wait 20 seconds and try again.", wait: 20 }, { status: 429 }),
      ),
    );
    openSetup();
    const user = userEvent.setup();
    await fill(user, "CODE", "x", "x{Enter}");
    expect(await screen.findByRole("button", { name: "Wait 20 s" })).toBeDisabled();
  });
});
