import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import { FieldRenderer } from "../src/components/forms/FieldRenderer";
import type { EditorValue } from "../src/components/forms/FieldRenderer";
import type { FieldSchema, FieldState } from "../src/types/api";

function field(overrides: Partial<FieldSchema>): FieldSchema {
  return {
    key: "f",
    label: "Field label",
    kind: "text",
    default: null,
    help: "",
    secret: false,
    env_var: null,
    options: [],
    bounds: null,
    max_length: 1000,
    max_items: 200,
    multiline: false,
    fields: [],
    id_key: null,
    readonly: false,
    risk: null,
    risk_when: [],
    ...overrides,
  };
}

const own: FieldState = { origin: "file", locked: false, pending: null };

function Harness(props: {
  schema: FieldSchema;
  initial: EditorValue;
  state?: FieldState;
  error?: string;
  onChange?: (v: EditorValue) => void;
  onRestore?: (key: string) => void;
}) {
  const [value, setValue] = useState<EditorValue>(props.initial);
  return (
    <FieldRenderer
      field={props.schema}
      value={value}
      state={props.state}
      error={props.error}
      onChange={(next) => {
        setValue(next);
        props.onChange?.(next);
      }}
      onRestore={props.onRestore}
    />
  );
}

describe("each kind renders its control and sends what was typed", () => {
  it("text sends the raw text", async () => {
    const onChange = vi.fn();
    render(<Harness schema={field({ kind: "text" })} initial="" onChange={onChange} />);
    await userEvent.type(screen.getByLabelText("Field label"), " ab c ");
    expect(onChange).toHaveBeenLastCalledWith(" ab c ");
  });

  it("multiline text is a text area", () => {
    render(<Harness schema={field({ kind: "text", multiline: true })} initial="line one" />);
    const area = screen.getByLabelText("Field label");
    expect(area.tagName).toBe("TEXTAREA");
    expect(area).toHaveValue("line one");
  });

  it("text carries the length limit", () => {
    render(<Harness schema={field({ kind: "text", max_length: 64 })} initial="" />);
    expect(screen.getByLabelText("Field label")).toHaveAttribute("maxlength", "64");
  });

  it("int keeps the raw text, even what is not a number, and asks for a numeric keypad", async () => {
    const onChange = vi.fn();
    render(<Harness schema={field({ kind: "int" })} initial={5} onChange={onChange} />);
    const input = screen.getByLabelText("Field label");
    expect(input).toHaveValue("5");
    expect(input).toHaveAttribute("inputmode", "numeric");
    await userEvent.type(input, "x7");
    expect(onChange).toHaveBeenLastCalledWith("5x7");
  });

  it("float keeps a comma as typed", async () => {
    const onChange = vi.fn();
    render(<Harness schema={field({ kind: "float" })} initial="" onChange={onChange} />);
    const input = screen.getByLabelText("Field label");
    expect(input).toHaveAttribute("inputmode", "decimal");
    await userEvent.type(input, "1,5");
    expect(onChange).toHaveBeenLastCalledWith("1,5");
  });

  it("shows the allowed range of a bounded number", () => {
    render(<Harness schema={field({ kind: "int", bounds: [1, 36500] })} initial="" />);
    expect(screen.getByText("Allowed range: 1 to 36500.")).toBeInTheDocument();
    expect(screen.getByLabelText("Field label")).toHaveAccessibleDescription(/Allowed range: 1 to 36500\./);
  });

  it("bool is a switch that a click and the space key both flip", async () => {
    const onChange = vi.fn();
    render(<Harness schema={field({ kind: "bool" })} initial={false} onChange={onChange} />);
    const toggle = screen.getByRole("switch", { name: "Field label" });
    expect(toggle).not.toBeChecked();
    await userEvent.click(toggle);
    expect(onChange).toHaveBeenLastCalledWith(true);
    toggle.focus();
    await userEvent.keyboard(" ");
    expect(onChange).toHaveBeenLastCalledWith(false);
  });

  it("a choice of up to four options is a radio group", async () => {
    const onChange = vi.fn();
    render(<Harness schema={field({ kind: "choice", options: ["denylist", "allowlist"] })} initial="denylist" onChange={onChange} />);
    const group = screen.getByRole("radiogroup", { name: "Field label" });
    expect(within(group).getByRole("radio", { name: "denylist" })).toBeChecked();
    await userEvent.click(within(group).getByRole("radio", { name: "allowlist" }));
    expect(onChange).toHaveBeenLastCalledWith("allowlist");
    expect(within(group).getByRole("radio", { name: "allowlist" })).toBeChecked();
  });

  it("a choice of more than four options is a select", async () => {
    const onChange = vi.fn();
    render(<Harness schema={field({ kind: "choice", options: ["a", "b", "c", "d", "e"] })} initial="a" onChange={onChange} />);
    expect(screen.queryByRole("radiogroup")).toBeNull();
    await userEvent.click(screen.getByRole("combobox", { name: "Field label" }));
    await userEvent.click(await screen.findByRole("option", { name: "d" }));
    expect(onChange).toHaveBeenLastCalledWith("d");
  });

  it("multichoice is a group of check boxes and sends the chosen options in option order", async () => {
    const onChange = vi.fn();
    render(<Harness schema={field({ kind: "multichoice", options: ["image", "video", "audio"] })} initial={["audio"]} onChange={onChange} />);
    const group = screen.getByRole("group", { name: "Field label" });
    expect(within(group).getByRole("checkbox", { name: "audio" })).toBeChecked();
    await userEvent.click(within(group).getByRole("checkbox", { name: "image" }));
    expect(onChange).toHaveBeenLastCalledWith(["image", "audio"]);
    await userEvent.click(within(group).getByRole("checkbox", { name: "audio" }));
    expect(onChange).toHaveBeenLastCalledWith(["image"]);
  });
});

describe("the default beside a value that is no longer it (CFG-01)", () => {
  const saved: FieldState = { origin: "file", locked: false, pending: null };
  const environment: FieldState = { origin: "environment", locked: true, pending: null };

  it.each([
    [field({ kind: "choice", options: ["a", "b"], default: "a" }), "b", "Default: a"],
    [field({ kind: "int", default: 8765 }), "9000", "Default: 8765"],
    [field({ kind: "int", default: null }), "30", "Default: None"],
    [field({ kind: "bool", default: true }), false, "Default: On"],
    [field({ kind: "multichoice", options: ["x", "y", "z"], default: ["x", "z"] }), ["y"], "Default: x, z"],
    [field({ kind: "list", default: [] }), ["one"], "Default: None"],
  ])("shows the default of a saved field: %#", (schema, initial, text) => {
    render(<Harness schema={schema} initial={initial} state={saved} />);
    expect(screen.getByText(text)).toBeInTheDocument();
  });

  it("shows it for a value that the environment sets", () => {
    render(<Harness schema={field({ kind: "text", default: "127.0.0.1", env_var: "V" })} initial="0.0.0.0" state={environment} />);
    expect(screen.getByText("Default: 127.0.0.1")).toBeInTheDocument();
  });

  it("shows none while the value is the default, and none for a secret", () => {
    const { rerender } = render(<Harness schema={field({ kind: "int", default: 5 })} initial="5" state={{ origin: "default", locked: false, pending: null }} />);
    expect(screen.queryByText(/^Default:/)).not.toBeInTheDocument();
    rerender(<Harness schema={field({ kind: "secret", secret: true })} initial={{ set: true }} state={saved} />);
    expect(screen.queryByText(/^Default:/)).not.toBeInTheDocument();
  });
});

describe("label in name (WCAG 2.5.3)", () => {
  it("the restore button's accessible name contains the text it shows", () => {
    render(<Harness schema={field({ label: "Mode" })} initial="x" state={own} onRestore={vi.fn()} />);
    const button = screen.getByRole("button", { name: "Restore the default of Mode" });
    expect(button.textContent).toBe("Restore the default");
    expect(button.getAttribute("aria-label")).toContain(button.textContent);
  });
});

describe("help, origin and errors", () => {
  it("UXP-43 a long unbroken word in the help or the lock note wraps instead of widening the page (a variable name on a 180 px screen)", () => {
    render(<Harness schema={field({ help: "WHATSAPP_SESSION_TAINT_AND_OTHER_LONG_VARIABLE_NAME wins." })} initial="x" />);
    expect(document.querySelector("[data-field]")).toHaveClass("[overflow-wrap:anywhere]");
  });

  it("links the help text to the control", () => {
    render(<Harness schema={field({ help: "Where files go." })} initial="" />);
    expect(screen.getByLabelText("Field label")).toHaveAccessibleDescription("Where files go.");
  });

  it.each([
    ["default", "Default"],
    ["file", "Saved"],
    ["environment", "Environment"],
  ] as const)("shows the %s origin", (origin, text) => {
    render(<Harness schema={field({})} initial="" state={{ origin, locked: false, pending: null }} />);
    expect(screen.getByText(text)).toBeInTheDocument();
  });

  it("shows the server's message next to the field and marks the control invalid", () => {
    render(<Harness schema={field({ help: "Help." })} initial="x" error="must be a whole number" />);
    const input = screen.getByLabelText("Field label");
    expect(input).toHaveAttribute("aria-invalid", "true");
    expect(input).toHaveAccessibleDescription(/Help\..*must be a whole number/);
    expect(screen.getByText("must be a whole number")).toBeVisible();
  });
});

describe("locked and read-only", () => {
  it("a field locked by the environment is read-only and names the variable", async () => {
    const onChange = vi.fn();
    const schema = field({ kind: "int", env_var: "WHATSAPP_MEDIA_RETENTION_DAYS" });
    render(<Harness schema={schema} initial="30" state={{ origin: "environment", locked: true, pending: null }} onChange={onChange} />);
    const input = screen.getByLabelText("Field label");
    expect(input).toHaveAttribute("readonly");
    await userEvent.type(input, "9");
    expect(onChange).not.toHaveBeenCalled();
    expect(input).toHaveValue("30");
    expect(screen.getByText(/WHATSAPP_MEDIA_RETENTION_DAYS/)).toBeInTheDocument();
    expect(input).toHaveAccessibleDescription(/WHATSAPP_MEDIA_RETENTION_DAYS/);
  });

  it("locks every kind of control", () => {
    const locked: FieldState = { origin: "environment", locked: true, pending: null };
    const { unmount } = render(<Harness schema={field({ kind: "bool", env_var: "V" })} initial state={locked} />);
    expect(screen.getByRole("switch")).toBeDisabled();
    unmount();
    const radios = render(<Harness schema={field({ kind: "choice", options: ["a", "b"], env_var: "V" })} initial="a" state={locked} />);
    expect(screen.getByRole("radio", { name: "a" })).toBeDisabled();
    radios.unmount();
    render(<Harness schema={field({ kind: "multichoice", options: ["a"], env_var: "V" })} initial={[]} state={locked} />);
    expect(screen.getByRole("checkbox", { name: "a" })).toBeDisabled();
  });

  it("a read-only field cannot be edited and says so", async () => {
    const onChange = vi.fn();
    render(<Harness schema={field({ readonly: true })} initial="fixed" onChange={onChange} />);
    await userEvent.type(screen.getByLabelText("Field label"), "z");
    expect(onChange).not.toHaveBeenCalled();
    expect(screen.getByText("Read only.")).toBeInTheDocument();
  });

  it("offers no restore on a locked field", () => {
    render(<Harness schema={field({ env_var: "V" })} initial="" state={{ origin: "environment", locked: true, pending: null }} onRestore={vi.fn()} />);
    expect(screen.queryByRole("button", { name: /Restore/ })).toBeNull();
  });
});

describe("risky values", () => {
  const risky = field({ kind: "choice", options: ["block", "off"], risk: "Turning this off lets text pass between chats.", risk_when: ["off"] });

  it("asks before a risky choice is applied, and applies it on confirm", async () => {
    const onChange = vi.fn();
    render(<Harness schema={risky} initial="block" onChange={onChange} />);
    await userEvent.click(screen.getByRole("radio", { name: "off" }));
    const dialog = await screen.findByRole("alertdialog");
    expect(dialog).toHaveTextContent("Turning this off lets text pass between chats.");
    expect(onChange).not.toHaveBeenCalled();
    expect(screen.getByRole("radio", { name: "block", hidden: true })).toBeChecked();
    await userEvent.click(within(dialog).getByRole("button", { name: "Use this value" }));
    expect(onChange).toHaveBeenCalledWith("off");
    expect(screen.getByRole("radio", { name: "off" })).toBeChecked();
  });

  it("leaves the value alone when the operator cancels", async () => {
    const onChange = vi.fn();
    render(<Harness schema={risky} initial="block" onChange={onChange} />);
    await userEvent.click(screen.getByRole("radio", { name: "off" }));
    await userEvent.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Cancel" }));
    expect(onChange).not.toHaveBeenCalled();
    expect(screen.getByRole("radio", { name: "block" })).toBeChecked();
  });

  it("does not ask for a value that is not risky", async () => {
    const onChange = vi.fn();
    render(<Harness schema={risky} initial="off" onChange={onChange} />);
    await userEvent.click(screen.getByRole("radio", { name: "block" }));
    expect(screen.queryByRole("alertdialog")).toBeNull();
    expect(onChange).toHaveBeenCalledWith("block");
  });

  it("asks for a risky boolean", async () => {
    const onChange = vi.fn();
    const schema = field({ kind: "bool", risk: "A reply may carry content between chats.", risk_when: [true] });
    render(<Harness schema={schema} initial={false} onChange={onChange} />);
    await userEvent.click(screen.getByRole("switch"));
    expect(await screen.findByRole("alertdialog")).toHaveTextContent("A reply may carry content between chats.");
    expect(onChange).not.toHaveBeenCalled();
  });

  it("keeps showing the warning while the value is risky", () => {
    render(<Harness schema={risky} initial="off" />);
    expect(screen.getByText("Turning this off lets text pass between chats.")).toBeVisible();
  });
});

describe("restore default", () => {
  it("asks first, and tells the parent only after the confirmation", async () => {
    const onRestore = vi.fn();
    render(<Harness schema={field({ key: "mode" })} initial="x" state={own} onRestore={onRestore} />);
    await userEvent.click(screen.getByRole("button", { name: "Restore the default of Field label" }));
    const dialog = await screen.findByRole("alertdialog");
    expect(onRestore).not.toHaveBeenCalled();
    await userEvent.click(within(dialog).getByRole("button", { name: "Restore default" }));
    expect(onRestore).toHaveBeenCalledWith("mode");
  });

  it("does nothing when the operator cancels", async () => {
    const onRestore = vi.fn();
    render(<Harness schema={field({})} initial="x" state={own} onRestore={onRestore} />);
    await userEvent.click(screen.getByRole("button", { name: /Restore the default/ }));
    await userEvent.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Cancel" }));
    expect(onRestore).not.toHaveBeenCalled();
  });

  it("is offered only for a value saved in the file", () => {
    render(<Harness schema={field({})} initial="x" state={{ origin: "default", locked: false, pending: null }} onRestore={vi.fn()} />);
    expect(screen.queryByRole("button", { name: /Restore/ })).toBeNull();
  });
});

describe("unknown kind", () => {
  it("renders a notice with the label and no control, without crashing", () => {
    const odd = field({ kind: "hologram" as never, label: "Strange" });
    render(<Harness schema={odd} initial="x" />);
    expect(screen.getByText("Strange")).toBeInTheDocument();
    expect(screen.getByText("This kind of field is not supported by this version of the interface.")).toBeInTheDocument();
    expect(screen.queryByRole("textbox")).toBeNull();
  });
});
