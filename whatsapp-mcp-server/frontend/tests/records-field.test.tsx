import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import { blankRecord, changeFor, editorFromValue } from "../src/components/forms/editor";
import { FieldRenderer } from "../src/components/forms/FieldRenderer";
import type { FieldSchema } from "../src/types/api";

function f(overrides: Partial<FieldSchema>): FieldSchema {
  return {
    key: "x", label: "X", kind: "text", default: null, help: "", secret: false, env_var: null, options: [], bounds: null,
    max_length: 1000, max_items: 200, multiline: false, fields: [], id_key: null, readonly: false, risk: null, risk_when: [],
    ...overrides,
  };
}

const profiles = f({
  key: "profiles",
  label: "Profiles",
  kind: "records",
  id_key: "name",
  fields: [
    f({ key: "name", label: "Name", kind: "text" }),
    f({ key: "enabled", label: "Enabled", kind: "bool", default: true }),
    f({ key: "mode", label: "Mode", kind: "choice", options: ["draft", "auto"], default: "draft" }),
    f({ key: "targets", label: "Targets", kind: "list" }),
    f({ key: "token", label: "Approval token", kind: "secret" }),
    f({ key: "limit", label: "Limit", kind: "int", bounds: [1, 100] }),
  ],
});

const loaded = [
  { name: "alpha", enabled: true, mode: "draft", targets: ["a@s"], token: { set: true }, limit: 5 },
  { name: "beta", enabled: false, mode: "auto", targets: [], token: { set: true }, limit: null },
];

function Harness({ errors, onChange }: { errors?: Record<string, string>; onChange?: (v: unknown) => void }) {
  const [value, setValue] = useState<unknown>(editorFromValue(profiles, loaded));
  return (
    <FieldRenderer
      field={profiles}
      value={value}
      loaded={loaded}
      errors={errors}
      onChange={(next) => {
        setValue(next);
        onChange?.(next);
      }}
    />
  );
}

describe("records field", () => {
  it("shows one card per record, named by its id, each with every sub-field", () => {
    render(<Harness />);
    const alpha = screen.getByRole("region", { name: "alpha" });
    expect(within(alpha).getByLabelText("Name")).toHaveValue("alpha");
    expect(within(alpha).getByRole("switch", { name: "Enabled" })).toBeChecked();
    expect(within(alpha).getByRole("radiogroup", { name: "Mode" })).toBeInTheDocument();
    expect(within(alpha).getByRole("textbox", { name: "Targets, item 1" })).toHaveValue("a@s");
    expect(within(alpha).getByRole("group", { name: "Approval token" })).toHaveTextContent("Set");
    expect(within(alpha).getByLabelText("Limit")).toHaveValue("5");
    expect(screen.getByRole("region", { name: "beta" })).toBeInTheDocument();
  });

  it("Add creates a blank record with every sub-field at its default and focuses it", async () => {
    const onChange = vi.fn();
    render(<Harness onChange={onChange} />);
    await userEvent.click(screen.getByRole("button", { name: "Add record to Profiles" }));
    const added = (onChange.mock.lastCall as [Record<string, unknown>[]])[0].at(-1);
    expect(added).toEqual({ name: "", enabled: true, mode: "draft", targets: [], token: { set: false }, limit: "" });
    const card = screen.getByRole("region", { name: "Profiles 3" });
    expect(card).toHaveFocus();
    expect(within(card).getByLabelText("Name")).toHaveValue("");
    expect(within(card).getByRole("group", { name: "Approval token" })).toHaveTextContent("Not set");
  });

  it("Remove asks first and keeps the other records' secrets", async () => {
    const onChange = vi.fn();
    render(<Harness onChange={onChange} />);
    await userEvent.click(screen.getByRole("button", { name: "Remove alpha" }));
    const dialog = await screen.findByRole("alertdialog");
    expect(dialog).toHaveTextContent("alpha will be removed from Profiles.");
    expect(onChange).not.toHaveBeenCalled();
    await userEvent.click(within(dialog).getByRole("button", { name: "Remove" }));
    const left = (onChange.mock.lastCall as [Record<string, unknown>[]])[0];
    expect(left).toHaveLength(1);
    expect(left[0]).toMatchObject({ name: "beta", token: { set: true } });
    expect(screen.queryByRole("region", { name: "alpha" })).toBeNull();
    const beta = screen.getByRole("region", { name: "beta" });
    expect(within(beta).getByRole("group", { name: "Approval token" })).toHaveTextContent("Set");
    expect(changeFor(profiles, left)).toEqual([
      { name: "beta", enabled: false, mode: "auto", targets: [], token: { set: true }, limit: null },
    ]);
  });

  it("a card keeps its own secret edit when an earlier card is removed", async () => {
    render(<Harness />);
    const beta = screen.getByRole("region", { name: "beta" });
    await userEvent.click(within(beta).getByRole("button", { name: "Clear" }));
    expect(within(beta).getByText("Will be removed")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Remove alpha" }));
    await userEvent.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Remove" }));
    expect(within(screen.getByRole("region", { name: "beta" })).getByText("Will be removed")).toBeInTheDocument();
  });

  it("keeps the record when the removal is cancelled", async () => {
    const onChange = vi.fn();
    render(<Harness onChange={onChange} />);
    await userEvent.click(screen.getByRole("button", { name: "Remove alpha" }));
    await userEvent.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Cancel" }));
    expect(onChange).not.toHaveBeenCalled();
    expect(screen.getByRole("region", { name: "alpha" })).toBeInTheDocument();
  });

  it("shows a sub-field's message on its own card only", () => {
    render(<Harness errors={{ "profiles.1.targets": "must list at least one chat", "profiles.1": "needs a target" }} />);
    const alpha = screen.getByRole("region", { name: "alpha" });
    const beta = screen.getByRole("region", { name: "beta" });
    expect(within(beta).getByText("must list at least one chat")).toBeVisible();
    expect(within(beta).getByText("needs a target")).toBeVisible();
    expect(within(alpha).queryByText("must list at least one chat")).toBeNull();
    expect(within(beta).getByRole("group", { name: "Targets" })).toHaveAccessibleDescription(/must list at least one chat/);
  });

  it("shows the message of the whole field", () => {
    render(<Harness errors={{ profiles: "needs at least one profile" }} />);
    expect(screen.getByText("needs at least one profile")).toBeVisible();
  });

  it("an edit in a card reaches only that record", async () => {
    const onChange = vi.fn();
    render(<Harness onChange={onChange} />);
    const beta = screen.getByRole("region", { name: "beta" });
    await userEvent.type(within(beta).getByLabelText("Limit"), "7");
    const records = (onChange.mock.lastCall as [Record<string, unknown>[]])[0];
    expect(records[1]).toMatchObject({ name: "beta", limit: "7" });
    expect(records[0]).toMatchObject({ name: "alpha", limit: "5" });
  });
});

describe("editor conversions", () => {
  it("blankRecord gives every sub-field its default form", () => {
    expect(blankRecord(profiles.fields)).toEqual({ name: "", enabled: true, mode: "draft", targets: [], token: { set: false }, limit: "" });
  });

  it("sends a record's numbers as numbers, lists as arrays, and secrets by the rules", () => {
    const edited = [
      { name: " gamma ", enabled: true, mode: "auto", targets: ["a", "", " b "], token: "new-secret", limit: "12" },
      { name: "delta", enabled: false, mode: "draft", targets: [], token: { clear: true }, limit: "" },
      { name: "eps", enabled: false, mode: "draft", targets: [], token: { set: false }, limit: "abc" },
    ];
    expect(changeFor(profiles, edited)).toEqual([
      { name: "gamma", enabled: true, mode: "auto", targets: ["a", "b"], token: "new-secret", limit: 12 },
      { name: "delta", enabled: false, mode: "draft", targets: [], token: { clear: true }, limit: null },
      { name: "eps", enabled: false, mode: "draft", targets: [], limit: "abc" },
    ]);
  });
});
