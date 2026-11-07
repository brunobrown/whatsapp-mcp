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

function openLogin() {
  const router = createAppRouter(createMemoryHistory({ initialEntries: ["/login"] }));
  render(
    <AppProviders router={router}>
      <RouterProvider router={router} />
    </AppProviders>,
  );
  return router;
}

function session(body: Record<string, unknown> = {}) {
  server.use(http.get("*/api/session", () => HttpResponse.json({ state: "login", csrf: "c0", ...body })));
}

beforeEach(() => {
  viewport = mockViewport(1280);
  queryClient.clear();
  server.use(http.get("*/api/sections", () => HttpResponse.json(sections)));
  session();
});
afterEach(() => {
  viewport.restore();
  vi.useRealTimers();
});

describe("sign-in page", () => {
  it("has one password field: type password, labelled, with the browser's autocomplete hint", async () => {
    openLogin();
    const field = await screen.findByLabelText("Password");
    expect(field).toHaveAttribute("type", "password");
    expect(field).toHaveAttribute("autocomplete", "current-password");
    expect(screen.getByRole("heading", { level: 1, name: "Sign in" })).toBeInTheDocument();
  });

  it("submits on Enter, sends the password and opens the first section", async () => {
    let body: unknown = null;
    server.use(
      http.post("*/api/session/login", async ({ request }) => {
        body = await request.json();
        return HttpResponse.json({ ok: true, csrf: "c1" });
      }),
    );
    const router = openLogin();
    await userEvent.type(await screen.findByLabelText("Password"), "correct horse{Enter}");
    await waitFor(() => {
      expect(router.state.location.pathname).toBe("/s/send_policy");
    });
    expect(body).toEqual({ password: "correct horse" });
  });

  it("also submits with the button", async () => {
    server.use(http.post("*/api/session/login", () => HttpResponse.json({ ok: true, csrf: "c1" })));
    const router = openLogin();
    await userEvent.type(await screen.findByLabelText("Password"), "pw");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
    await waitFor(() => {
      expect(router.state.location.pathname).toBe("/s/send_policy");
    });
  });

  it("shows the server's message for a wrong password, clears the field and keeps the focus there", async () => {
    server.use(http.post("*/api/session/login", () => HttpResponse.json({ ok: false, message: "That password is not right." })));
    const router = openLogin();
    const field = await screen.findByLabelText("Password");
    await userEvent.type(field, "nope{Enter}");
    expect(await screen.findByRole("alert")).toHaveTextContent("That password is not right.");
    expect(router.state.location.pathname).toBe("/login");
    expect(field).toHaveValue("");
    expect(field).toHaveFocus();
    expect(field).toHaveAttribute("aria-invalid", "true");
  });

  it("does not send an empty password", async () => {
    let calls = 0;
    server.use(
      http.post("*/api/session/login", () => {
        calls += 1;
        return HttpResponse.json({ ok: false });
      }),
    );
    openLogin();
    await screen.findByLabelText("Password");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(calls).toBe(0);
  });

  it("shows the message of a throttled attempt and disables the button for the wait, counting down", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    server.use(
      http.post("*/api/session/login", () =>
        HttpResponse.json({ ok: false, message: "Too many attempts. Wait 3 seconds.", wait: 3 }, { status: 429 }),
      ),
    );
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    openLogin();
    await user.type(await screen.findByLabelText("Password"), "x{Enter}");
    expect(await screen.findByRole("alert")).toHaveTextContent("Too many attempts. Wait 3 seconds.");
    const button = screen.getByRole("button", { name: "Wait 3 s" });
    expect(button).toBeDisabled();
    act(() => {
      vi.advanceTimersByTime(1000);
    });
    expect(screen.getByRole("button", { name: "Wait 2 s" })).toBeDisabled();
    act(() => {
      vi.advanceTimersByTime(2000);
    });
    expect(screen.getByRole("button", { name: "Sign in" })).toBeEnabled();
  });

  it("also honours a wait that arrives with a refusal answered 200", async () => {
    server.use(http.post("*/api/session/login", () => HttpResponse.json({ ok: false, message: "No.", wait: 30 })));
    openLogin();
    await userEvent.type(await screen.findByLabelText("Password"), "x{Enter}");
    expect(await screen.findByRole("button", { name: "Wait 30 s" })).toBeDisabled();
  });

  it("shows the session message, such as the inactivity notice", async () => {
    session({ message: "Your session ended because of inactivity." });
    openLogin();
    expect(await screen.findByText("Your session ended because of inactivity.")).toBeInTheDocument();
    expect(screen.getByLabelText("Password")).toBeInTheDocument();
  });

  it("shows the lost connection instead of a refusal when the call fails", async () => {
    server.use(http.post("*/api/session/login", () => HttpResponse.error()));
    openLogin();
    await userEvent.type(await screen.findByLabelText("Password"), "x{Enter}");
    expect(await screen.findByRole("alert")).toHaveTextContent("The interface lost its connection.");
  });

  it("shows only the server's reason when sign-in is broken", async () => {
    session({ state: "broken", message: "The password file is damaged." });
    openLogin();
    expect(await screen.findByRole("alert")).toHaveTextContent("The password file is damaged.");
    expect(screen.queryByLabelText("Password")).toBeNull();
  });
});
