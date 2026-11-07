import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import { FieldRenderer } from "../src/components/forms/FieldRenderer";
import { SecretField } from "../src/components/forms/SecretField";
import type { SecretDraft } from "../src/components/forms/SecretField";
import type { FieldSchema } from "../src/types/api";

const schema: FieldSchema = {
  key: "override_token",
  label: "Override token",
  kind: "secret",
  default: null,
  help: "",
  secret: true,
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
};

function Harness({ loaded, onChange, disabled = false }: { loaded: unknown; onChange?: (v: SecretDraft) => void; disabled?: boolean }) {
  const [value, setValue] = useState<unknown>(loaded);
  return (
    <SecretField
      label="Override token"
      id="tok"
      value={value}
      loaded={loaded}
      disabled={disabled}
      onChange={(next) => {
        setValue(next);
        onChange?.(next);
      }}
    />
  );
}

describe("secret field", () => {
  it("shows Set and no text for a secret that is set", () => {
    const { container } = render(<Harness loaded={{ set: true, value: "hunter2-stored" }} />);
    expect(screen.getByText("Set")).toBeInTheDocument();
    expect(screen.queryByText(/hunter2/)).toBeNull();
    expect(container.querySelector("input")).toBeNull();
    expect(screen.getByRole("button", { name: "Replace" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Clear" })).toBeInTheDocument();
  });

  it("shows Not set, offers to set a value and offers no Clear", () => {
    render(<Harness loaded={{ set: false }} />);
    expect(screen.getByText("Not set")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Set a value" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Clear" })).toBeNull();
  });

  it("sends nothing while untouched", () => {
    const onChange = vi.fn();
    render(<Harness loaded={{ set: true }} onChange={onChange} />);
    expect(onChange).not.toHaveBeenCalled();
  });

  it("Replace opens an empty, focused password input that browsers do not autofill, and sends the new string", async () => {
    const onChange = vi.fn();
    render(<Harness loaded={{ set: true }} onChange={onChange} />);
    await userEvent.click(screen.getByRole("button", { name: "Replace" }));
    const input = screen.getByLabelText("New value for Override token");
    expect(input).toHaveAttribute("type", "password");
    expect(input).toHaveAttribute("autocomplete", "new-password");
    expect(input).toHaveValue("");
    expect(input).toHaveFocus();
    expect(screen.getByText("Will be replaced")).toBeInTheDocument();
    await userEvent.type(input, "n3w secret");
    expect(onChange).toHaveBeenLastCalledWith("n3w secret");
    expect(input).toHaveValue("n3w secret");
  });

  it("goes back to untouched when the typed text is emptied or the replace is cancelled", async () => {
    const onChange = vi.fn();
    render(<Harness loaded={{ set: true }} onChange={onChange} />);
    await userEvent.click(screen.getByRole("button", { name: "Replace" }));
    const input = screen.getByLabelText("New value for Override token");
    await userEvent.type(input, "a");
    await userEvent.clear(input);
    expect(onChange).toHaveBeenLastCalledWith({ set: true });
    await userEvent.type(input, "b");
    await userEvent.click(screen.getByRole("button", { name: "Keep the current one" }));
    expect(onChange).toHaveBeenLastCalledWith({ set: true });
    expect(screen.queryByLabelText("New value for Override token")).toBeNull();
    expect(screen.getByText("Set")).toBeInTheDocument();
  });

  it("Clear sends {clear: true} and Undo takes it back", async () => {
    const onChange = vi.fn();
    render(<Harness loaded={{ set: true }} onChange={onChange} />);
    await userEvent.click(screen.getByRole("button", { name: "Clear" }));
    expect(onChange).toHaveBeenLastCalledWith({ clear: true });
    expect(screen.getByText("Will be removed")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Undo" }));
    expect(onChange).toHaveBeenLastCalledWith({ set: true });
    expect(screen.getByText("Set")).toBeInTheDocument();
  });

  it("a secret that is not set can get a value", async () => {
    const onChange = vi.fn();
    render(<Harness loaded={{ set: false }} onChange={onChange} />);
    await userEvent.click(screen.getByRole("button", { name: "Set a value" }));
    await userEvent.type(screen.getByLabelText("New value for Override token"), "abc");
    expect(onChange).toHaveBeenLastCalledWith("abc");
  });

  it("disables every action when locked", () => {
    render(<Harness loaded={{ set: true }} disabled />);
    expect(screen.getByRole("button", { name: "Replace" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Clear" })).toBeDisabled();
  });

  it("works from the keyboard", async () => {
    const onChange = vi.fn();
    render(<Harness loaded={{ set: true }} onChange={onChange} />);
    await userEvent.tab();
    expect(screen.getByRole("button", { name: "Replace" })).toHaveFocus();
    await userEvent.tab();
    await userEvent.keyboard("{Enter}");
    expect(onChange).toHaveBeenLastCalledWith({ clear: true });
  });
});

describe("secret field in a form", () => {
  it("FieldRenderer renders the secret kind as a named group with the server's message", () => {
    render(
      <FieldRenderer
        field={schema}
        value={{ set: true }}
        loaded={{ set: true }}
        error="must be text"
        onChange={vi.fn()}
      />,
    );
    const group = screen.getByRole("group", { name: "Override token" });
    expect(within(group).getByText("Set")).toBeInTheDocument();
    expect(screen.getByText("must be text")).toBeVisible();
  });
});
