import { QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useSection } from "../src/features/sections/useSection";
import * as notices from "../src/lib/notices";
import { queryClient, SESSION_KEY } from "../src/lib/queryClient";
import { useUiStore } from "../src/stores/ui";
import type { FieldSchema, SaveOutcome, SectionView } from "../src/types/api";
import { server } from "./msw/server";

function f(overrides: Partial<FieldSchema>): FieldSchema {
  return {
    key: "x", label: "X", kind: "text", default: null, help: "", secret: false, env_var: null, options: [], bounds: null,
    max_length: 1000, max_items: 200, multiline: false, fields: [], id_key: null, readonly: false, risk: null, risk_when: [],
    ...overrides,
  };
}

const fields = [
  f({ key: "name", label: "Name", kind: "text", default: "none" }),
  f({ key: "limit", label: "Limit", kind: "int" }),
  f({ key: "mode", label: "Mode", kind: "choice", options: ["a", "b"], default: "a" }),
  f({ key: "deny", label: "Deny", kind: "list" }),
  f({ key: "token", label: "Token", kind: "secret" }),
  f({ key: "locked", label: "Locked", kind: "text", env_var: "V" }),
];

function view(version = "v1", values: Record<string, unknown> = {}): SectionView {
  return {
    id: "s", title: "S", description: "", restart: "bridge", status: "ready", banner: null, warnings: [], version,
    read_only: false, fields,
    values: { name: "alpha", limit: 5, mode: "a", deny: ["x"], token: { set: true }, locked: "env", ...values },
    state: {
      name: { origin: "file", locked: false, pending: null },
      limit: { origin: "default", locked: false, pending: null },
      mode: { origin: "file", locked: false, pending: null },
      deny: { origin: "default", locked: false, pending: null },
      token: { origin: "file", locked: false, pending: null },
      locked: { origin: "environment", locked: true, pending: null },
    },
  };
}

const okOutcome: SaveOutcome = { ok: true, unchanged: false, conflict: false, needs_confirmation: false, errors: {}, general: [], restarts: ["bridge"] };

let current: SectionView;
let reads: number;
let puts: unknown[];
let validations: unknown[];
let validateErrors: Record<string, string>;

function wrapper({ children }: { children: ReactNode }) {
  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
}

async function mount() {
  const hook = renderHook(() => useSection("s"), { wrapper });
  await waitFor(() => {
    expect(hook.result.current.view).toBeDefined();
  });
  return hook;
}

beforeEach(() => {
  queryClient.clear();
  queryClient.setQueryData(SESSION_KEY, { state: "authenticated", csrf: "c" });
  useUiStore.setState(useUiStore.getInitialState(), true);
  notices.resetNoticeMemory();
  current = view();
  reads = 0;
  puts = [];
  validations = [];
  validateErrors = {};
  server.use(
    http.get("*/api/sections/s", () => {
      reads += 1;
      return HttpResponse.json(current);
    }),
    http.post("*/api/sections/s/validate", async ({ request }) => {
      validations.push(await request.json());
      return HttpResponse.json({ errors: validateErrors, general: [], can_save: Object.keys(validateErrors).length === 0 });
    }),
  );
});
afterEach(() => {
  vi.useRealTimers();
});

describe("loading and dirty", () => {
  it("builds the draft from the loaded values and starts clean", async () => {
    const { result } = await mount();
    expect(result.current.draft).toEqual({
      name: "alpha", limit: "5", mode: "a", deny: ["x"], token: { set: true }, locked: "env",
    });
    expect(result.current.dirty).toBe(false);
  });

  it("is dirty only while the draft differs from the loaded values", async () => {
    const { result } = await mount();
    act(() => {
      result.current.setField("name", "beta");
    });
    expect(result.current.dirty).toBe(true);
    expect(result.current.draft["name"]).toBe("beta");
    act(() => {
      result.current.setField("name", "alpha");
    });
    expect(result.current.dirty).toBe(false);
  });

  it("discard returns to the loaded values", async () => {
    const { result } = await mount();
    act(() => {
      result.current.setField("limit", "9");
      result.current.setField("deny", ["x", "y"]);
    });
    expect(result.current.dirty).toBe(true);
    act(() => {
      result.current.discard();
    });
    expect(result.current.dirty).toBe(false);
    expect(result.current.draft["limit"]).toBe("5");
  });

  it("follows the server when the section is read again and nothing is edited", async () => {
    const { result } = await mount();
    current = view("v2", { name: "gamma" });
    await act(async () => {
      await result.current.query.refetch();
    });
    await waitFor(() => {
      expect(result.current.draft["name"]).toBe("gamma");
    });
    expect(result.current.view?.version).toBe("v2");
  });

  it("keeps the draft and the loaded version when the section is read again with edits pending", async () => {
    const { result } = await mount();
    act(() => {
      result.current.setField("name", "mine");
    });
    current = view("v2", { name: "theirs" });
    await act(async () => {
      await result.current.query.refetch();
    });
    await waitFor(() => {
      expect(result.current.query.data?.version).toBe("v2");
    });
    expect(result.current.draft["name"]).toBe("mine");
    expect(result.current.view?.version).toBe("v1");
  });
});

describe("validation", () => {
  it("runs 300 ms after the last change, once, with the changes as raw text", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const { result } = await mount();
    act(() => {
      result.current.setField("limit", "1");
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(200);
    });
    act(() => {
      result.current.setField("limit", "12x");
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(299);
    });
    expect(validations).toHaveLength(0);
    validateErrors = { limit: "must be a whole number" };
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2);
    });
    await waitFor(() => {
      expect(result.current.errors["limit"]).toBe("must be a whole number");
    });
    expect(validations).toEqual([{ version: "v1", changes: { limit: "12x" }, restore: [] }]);
  });

  it("does not validate a clean draft", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    await mount();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000);
    });
    expect(validations).toHaveLength(0);
  });

  it("drops the messages when the draft becomes clean again", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const { result } = await mount();
    validateErrors = { limit: "bad" };
    act(() => {
      result.current.setField("limit", "z");
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(400);
    });
    await waitFor(() => {
      expect(result.current.errors["limit"]).toBe("bad");
    });
    act(() => {
      result.current.setField("limit", "5");
    });
    expect(result.current.errors).toEqual({});
  });
});

describe("changes sent", () => {
  it("sends only the fields that changed: lists as lines without blanks, an untouched secret not at all", async () => {
    const { result } = await mount();
    server.use(
      http.put("*/api/sections/s", async ({ request }) => {
        puts.push(await request.json());
        return HttpResponse.json(okOutcome);
      }),
    );
    act(() => {
      result.current.setField("deny", ["x", "", "y"]);
      result.current.setField("mode", "b");
    });
    await act(async () => {
      await result.current.save();
    });
    expect(puts).toEqual([{ version: "v1", changes: { deny: "x\ny", mode: "b" }, restore: [] }]);
  });

  it("sends a new secret as text and a clear as {clear: true}", async () => {
    const { result } = await mount();
    server.use(
      http.put("*/api/sections/s", async ({ request }) => {
        puts.push(await request.json());
        return HttpResponse.json(okOutcome);
      }),
    );
    act(() => {
      result.current.setField("token", "new-secret");
    });
    await act(async () => {
      await result.current.save();
    });
    expect((puts[0] as { changes: unknown }).changes).toEqual({ token: "new-secret" });
    act(() => {
      result.current.setField("token", { clear: true });
    });
    await act(async () => {
      await result.current.save();
    });
    expect((puts[1] as { changes: unknown }).changes).toEqual({ token: { clear: true } });
  });
});

describe("saving", () => {
  beforeEach(() => {
    server.use(
      http.put("*/api/sections/s", async ({ request }) => {
        puts.push(await request.json());
        return HttpResponse.json(okOutcome);
      }),
    );
  });

  it("a successful save reads the section again, records the restart scopes and clears the draft", async () => {
    const { result } = await mount();
    act(() => {
      result.current.setField("name", "beta");
    });
    current = view("v2", { name: "beta" });
    const before = reads;
    await act(async () => {
      await result.current.save();
    });
    expect(reads).toBeGreaterThan(before);
    expect(useUiStore.getState().restart).toEqual(["bridge"]);
    expect(result.current.dirty).toBe(false);
    expect(result.current.draft["name"]).toBe("beta");
    expect(result.current.view?.version).toBe("v2");
    expect(result.current.saved).toBe(true);
  });

  it("a save refused with no field to blame (a file that cannot be written) raises one global notice", async () => {
    server.use(
      http.put("*/api/sections/s", () =>
        HttpResponse.json(
          { ...okOutcome, ok: false, restarts: [], general: ["the file could not be written (Permission denied)"] },
          { status: 422 },
        ),
      ),
    );
    const { result } = await mount();
    act(() => {
      result.current.setField("name", "beta");
    });
    await act(async () => {
      await result.current.save();
    });
    await act(async () => {
      await result.current.save();
    });
    expect(useUiStore.getState().notices.map((notice) => notice.text)).toEqual(["The changes could not be saved."]);
  });

  it("a refusal that names a field, or a conflict, raises no global notice", async () => {
    server.use(
      http.put("*/api/sections/s", () =>
        HttpResponse.json({ ...okOutcome, ok: false, restarts: [], errors: { limit: "must be a whole number" } }, { status: 422 }),
      ),
    );
    const { result } = await mount();
    act(() => {
      result.current.setField("limit", "x");
    });
    await act(async () => {
      await result.current.save();
    });
    server.use(http.put("*/api/sections/s", () => HttpResponse.json({ ...okOutcome, ok: false, conflict: true }, { status: 409 })));
    await act(async () => {
      await result.current.save();
    });
    expect(useUiStore.getState().notices).toEqual([]);
  });

  it("a save that changed nothing records no restart", async () => {
    server.use(http.put("*/api/sections/s", () => HttpResponse.json({ ...okOutcome, unchanged: true, restarts: [] })));
    const { result } = await mount();
    act(() => {
      result.current.setField("name", "beta");
    });
    await act(async () => {
      await result.current.save();
    });
    expect(useUiStore.getState().restart).toEqual([]);
  });

  it("a 409 conflict keeps the draft, exposes conflict and records nothing", async () => {
    server.use(
      http.put("*/api/sections/s", () =>
        HttpResponse.json({ ...okOutcome, ok: false, conflict: true, restarts: [] }, { status: 409 }),
      ),
    );
    const { result } = await mount();
    act(() => {
      result.current.setField("name", "beta");
    });
    const before = reads;
    await act(async () => {
      await result.current.save();
    });
    expect(result.current.conflict).toBe(true);
    expect(result.current.dirty).toBe(true);
    expect(result.current.draft["name"]).toBe("beta");
    expect(reads).toBe(before);
    expect(useUiStore.getState().restart).toEqual([]);
  });

  it("reload after a conflict drops the draft and reads the file again", async () => {
    server.use(http.put("*/api/sections/s", () => HttpResponse.json({ ...okOutcome, ok: false, conflict: true }, { status: 409 })));
    const { result } = await mount();
    act(() => {
      result.current.setField("name", "beta");
    });
    await act(async () => {
      await result.current.save();
    });
    current = view("v3", { name: "disk" });
    await act(async () => {
      await result.current.reload();
    });
    await waitFor(() => {
      expect(result.current.draft["name"]).toBe("disk");
    });
    expect(result.current.conflict).toBe(false);
    expect(result.current.dirty).toBe(false);
    expect(result.current.draft["name"]).toBe("disk");
  });

  it("asks for confirmation when the file cannot be read, and replaces it on a second save", async () => {
    let call = 0;
    server.use(
      http.put("*/api/sections/s", async ({ request }) => {
        puts.push(await request.json());
        call += 1;
        return call === 1
          ? HttpResponse.json({ ...okOutcome, ok: false, needs_confirmation: true, restarts: [] }, { status: 409 })
          : HttpResponse.json(okOutcome);
      }),
    );
    const { result } = await mount();
    act(() => {
      result.current.setField("name", "beta");
    });
    await act(async () => {
      await result.current.save();
    });
    expect(result.current.needsConfirmation).toBe(true);
    expect(result.current.dirty).toBe(true);
    await act(async () => {
      await result.current.save({ confirmReplace: true });
    });
    expect(puts[1]).toMatchObject({ confirm_replace: true });
    expect(result.current.needsConfirmation).toBe(false);
    expect(result.current.dirty).toBe(false);
  });

  it("a 422 exposes the field messages and keeps the draft; the next edit clears them", async () => {
    server.use(
      http.put("*/api/sections/s", () =>
        HttpResponse.json({ ...okOutcome, ok: false, errors: { limit: "must be a whole number" }, general: ["Fix it."], restarts: [] }, { status: 422 }),
      ),
    );
    const { result } = await mount();
    act(() => {
      result.current.setField("limit", "x");
    });
    await act(async () => {
      await result.current.save();
    });
    expect(result.current.errors).toEqual({ limit: "must be a whole number" });
    expect(result.current.general).toEqual(["Fix it."]);
    expect(result.current.dirty).toBe(true);
    act(() => {
      result.current.setField("limit", "7");
    });
    expect(result.current.errors).toEqual({});
  });
});

describe("restoring defaults", () => {
  it("restoring a field shows its default and sends it in restore; editing it again takes it out", async () => {
    const { result } = await mount();
    server.use(
      http.put("*/api/sections/s", async ({ request }) => {
        puts.push(await request.json());
        return HttpResponse.json(okOutcome);
      }),
    );
    act(() => {
      result.current.restoreField("name");
    });
    expect(result.current.draft["name"]).toBe("none");
    expect(result.current.dirty).toBe(true);
    expect(result.current.restored).toEqual(["name"]);
    act(() => {
      result.current.setField("name", "typed");
    });
    expect(result.current.restored).toEqual([]);
    act(() => {
      result.current.restoreField("name");
    });
    await act(async () => {
      await result.current.save();
    });
    expect(puts[0]).toEqual({ version: "v1", changes: {}, restore: ["name"] });
  });

  it("restoring the section skips locked and read-only fields and leaves defaults alone", async () => {
    const { result } = await mount();
    let skipped: string[] = [];
    act(() => {
      skipped = result.current.restoreAll();
    });
    expect(result.current.restored.sort()).toEqual(["mode", "name", "token"].sort());
    expect(skipped).toEqual(["locked"]);
    expect(result.current.draft["locked"]).toBe("env");
    expect(result.current.draft["name"]).toBe("none");
  });
});
