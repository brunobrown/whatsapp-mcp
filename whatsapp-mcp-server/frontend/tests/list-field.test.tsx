import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import { FieldRenderer } from "../src/components/forms/FieldRenderer";
import { joinLines, ListField } from "../src/components/forms/ListField";
import type { FieldSchema } from "../src/types/api";

function Harness({ initial, onChange, disabled = false, maxItems }: { initial: unknown; onChange?: (v: string[]) => void; disabled?: boolean; maxItems?: number }) {
  const [value, setValue] = useState<unknown>(initial);
  return (
    <ListField
      label="Deny"
      id="deny"
      value={value}
      disabled={disabled}
      {...(maxItems !== undefined && { maxItems })}
      onChange={(next) => {
        setValue(next);
        onChange?.(next);
      }}
    />
  );
}

const inputs = () => screen.getAllByRole("textbox").map((input) => (input as HTMLInputElement).value);

describe("list field", () => {
  it("shows one input per item, named by label and position", () => {
    render(<Harness initial={["a@s.whatsapp.net", "b@g.us"]} />);
    expect(screen.getByRole("textbox", { name: "Deny, item 1" })).toHaveValue("a@s.whatsapp.net");
    expect(screen.getByRole("textbox", { name: "Deny, item 2" })).toHaveValue("b@g.us");
  });

  it("says when the list is empty and still offers Add", () => {
    render(<Harness initial={[]} />);
    expect(screen.getByText("No items yet.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add item" })).toBeEnabled();
  });

  it("reads a text of lines as items", () => {
    render(<Harness initial={"one\ntwo"} />);
    expect(inputs()).toEqual(["one", "two"]);
  });

  it("adds an item with the keyboard and puts the focus in it", async () => {
    const onChange = vi.fn();
    render(<Harness initial={["a"]} onChange={onChange} />);
    await userEvent.tab(); // item
    await userEvent.tab(); // remove
    await userEvent.tab(); // add
    expect(screen.getByRole("button", { name: "Add item" })).toHaveFocus();
    await userEvent.keyboard("{Enter}");
    expect(onChange).toHaveBeenLastCalledWith(["a", ""]);
    expect(screen.getByRole("textbox", { name: "Deny, item 2" })).toHaveFocus();
    await userEvent.keyboard("zed");
    expect(onChange).toHaveBeenLastCalledWith(["a", "zed"]);
  });

  it("Enter inside an item adds the next item right after it instead of submitting a form", async () => {
    const onSubmit = vi.fn((event: { preventDefault: () => void }) => {
      event.preventDefault();
    });
    render(
      <form onSubmit={onSubmit}>
        <Harness initial={["a", "c"]} />
      </form>,
    );
    await userEvent.click(screen.getByRole("textbox", { name: "Deny, item 1" }));
    await userEvent.keyboard("{Enter}");
    expect(onSubmit).not.toHaveBeenCalled();
    expect(inputs()).toEqual(["a", "", "c"]);
    expect(screen.getByRole("textbox", { name: "Deny, item 2" })).toHaveFocus();
  });

  it("removes an empty item at once, by keyboard", async () => {
    const onChange = vi.fn();
    render(<Harness initial={["a", ""]} onChange={onChange} />);
    screen.getByRole("button", { name: "Remove item 2 of Deny" }).focus();
    await userEvent.keyboard(" ");
    expect(screen.queryByRole("alertdialog")).toBeNull();
    expect(onChange).toHaveBeenLastCalledWith(["a"]);
    expect(screen.getByRole("textbox", { name: "Deny, item 1" })).toHaveFocus();
  });

  it("asks before removing a filled item and removes it on confirm", async () => {
    const onChange = vi.fn();
    render(<Harness initial={["a", "keep", "c"]} onChange={onChange} />);
    await userEvent.click(screen.getByRole("button", { name: "Remove item 2 of Deny" }));
    const dialog = await screen.findByRole("alertdialog");
    expect(dialog).toHaveTextContent("“keep” will be removed from Deny.");
    expect(onChange).not.toHaveBeenCalled();
    await userEvent.click(within(dialog).getByRole("button", { name: "Remove" }));
    expect(onChange).toHaveBeenLastCalledWith(["a", "c"]);
    expect(inputs()).toEqual(["a", "c"]);
  });

  it("keeps the item when the removal is cancelled", async () => {
    const onChange = vi.fn();
    render(<Harness initial={["a", "keep"]} onChange={onChange} />);
    await userEvent.click(screen.getByRole("button", { name: "Remove item 2 of Deny" }));
    await userEvent.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Cancel" }));
    expect(onChange).not.toHaveBeenCalled();
    expect(inputs()).toEqual(["a", "keep"]);
  });

  it("moves the focus to Add when the last item goes", async () => {
    render(<Harness initial={[""]} />);
    await userEvent.click(screen.getByRole("button", { name: "Remove item 1 of Deny" }));
    expect(screen.getByRole("button", { name: "Add item" })).toHaveFocus();
  });

  it("stops adding at the item limit", () => {
    render(<Harness initial={["a", "b"]} maxItems={2} />);
    expect(screen.getByRole("button", { name: "Add item" })).toBeDisabled();
  });

  it("is read-only when locked", () => {
    render(<Harness initial={["a"]} disabled />);
    expect(screen.getByRole("textbox")).toHaveAttribute("readonly");
    expect(screen.getByRole("button", { name: "Add item" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Remove item 1 of Deny" })).toBeDisabled();
  });
});

describe("joinLines", () => {
  it("joins the items into lines and leaves out the empty ones", () => {
    expect(joinLines(["a", "", "  ", "b"])).toBe("a\nb");
    expect(joinLines([])).toBe("");
  });
});

describe("list field in a form", () => {
  it("FieldRenderer shows the list kind as a named group", () => {
    const schema = {
      key: "deny", label: "Blocked chats", kind: "list", default: [], help: "One per line.", secret: false, env_var: null,
      options: [], bounds: null, max_length: 1000, max_items: 200, multiline: false, fields: [], id_key: null,
      readonly: false, risk: null, risk_when: [],
    } satisfies FieldSchema;
    render(<FieldRenderer field={schema} value={["x"]} onChange={vi.fn()} />);
    const group = screen.getByRole("group", { name: "Blocked chats" });
    expect(within(group).getByRole("textbox", { name: "Blocked chats, item 1" })).toHaveValue("x");
    expect(group).toHaveAccessibleDescription("One per line.");
  });
});
