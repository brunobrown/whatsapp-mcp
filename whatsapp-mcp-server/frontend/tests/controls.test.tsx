import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import { CheckboxGroup, Select, Switch } from "../src/components/ui/controls";

const options = [
  { value: "off", label: "Off" },
  { value: "ask", label: "Ask first" },
  { value: "auto", label: "Automatic" },
];

describe("Switch", () => {
  it("is a switch named by its label and starts off", () => {
    render(
      <>
        <label htmlFor="dry">Dry run</label>
        <Switch id="dry" checked={false} onCheckedChange={() => undefined} />
      </>,
    );
    expect(screen.getByRole("switch", { name: "Dry run" })).toHaveAttribute("aria-checked", "false");
  });

  it("toggles with a click and with Space, reporting the new value", async () => {
    const onChange = vi.fn();
    function Harness() {
      const [on, setOn] = useState(false);
      return (
        <Switch
          aria-label="Dry run"
          checked={on}
          onCheckedChange={(value) => {
            onChange(value);
            setOn(value);
          }}
        />
      );
    }
    render(<Harness />);
    const control = screen.getByRole("switch", { name: "Dry run" });
    await userEvent.click(control);
    expect(control).toHaveAttribute("aria-checked", "true");
    await userEvent.keyboard(" ");
    expect(control).toHaveAttribute("aria-checked", "false");
    expect(onChange.mock.calls).toEqual([[true], [false]]);
  });

  it("is reached by Tab and does not toggle when disabled", async () => {
    const onChange = vi.fn();
    render(<Switch aria-label="Dry run" checked={false} disabled onCheckedChange={onChange} />);
    const control = screen.getByRole("switch", { name: "Dry run" });
    expect(control).toBeDisabled();
    await userEvent.click(control);
    expect(onChange).not.toHaveBeenCalled();
  });

  it("follows aria-invalid and aria-describedby", () => {
    render(
      <>
        <Switch aria-label="Dry run" checked onCheckedChange={() => undefined} aria-invalid="true" aria-describedby="why" />
        <p id="why">Cannot be turned on here.</p>
      </>,
    );
    const control = screen.getByRole("switch", { name: "Dry run" });
    expect(control).toBeInvalid();
    expect(control).toHaveAccessibleDescription("Cannot be turned on here.");
  });
});

describe("Select", () => {
  function Harness({ onChange = () => undefined }: { onChange?: (value: string) => void }) {
    const [value, setValue] = useState("off");
    return (
      <>
        <label htmlFor="mode">Mode</label>
        <Select
          id="mode"
          value={value}
          options={options}
          onValueChange={(next) => {
            setValue(next);
            onChange(next);
          }}
        />
      </>
    );
  }

  it("is a combobox named by its label and shows the selected option", () => {
    render(<Harness />);
    expect(screen.getByRole("combobox", { name: "Mode" })).toHaveTextContent("Off");
  });

  it("opens a listbox of options on click and selects one with a click", async () => {
    const onChange = vi.fn();
    render(<Harness onChange={onChange} />);
    await userEvent.click(screen.getByRole("combobox", { name: "Mode" }));
    const list = await screen.findByRole("listbox");
    expect(within(list).getAllByRole("option").map((o) => o.textContent)).toEqual(["Off", "Ask first", "Automatic"]);
    await userEvent.click(within(list).getByRole("option", { name: "Automatic" }));
    expect(onChange).toHaveBeenCalledWith("auto");
    expect(screen.getByRole("combobox", { name: "Mode" })).toHaveTextContent("Automatic");
  });

  it("works by keyboard: arrows move, Enter chooses, Escape closes without choosing", async () => {
    const onChange = vi.fn();
    render(<Harness onChange={onChange} />);
    await userEvent.tab();
    expect(screen.getByRole("combobox", { name: "Mode" })).toHaveFocus();

    await userEvent.keyboard("{ArrowDown}");
    expect(await screen.findByRole("listbox")).toBeInTheDocument();
    await userEvent.keyboard("{ArrowDown}{Enter}");
    expect(onChange).toHaveBeenCalledWith("ask");
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "Mode" })).toHaveFocus();

    await userEvent.keyboard("{ArrowDown}");
    await screen.findByRole("listbox");
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
    expect(onChange).toHaveBeenCalledTimes(1);
  });

  it("opens with Space as well", async () => {
    render(<Harness />);
    await userEvent.tab();
    await userEvent.keyboard(" ");
    expect(await screen.findByRole("listbox")).toBeInTheDocument();
  });

  it("follows aria-invalid and aria-describedby on the trigger", () => {
    render(
      <>
        <Select
          aria-label="Mode"
          value="off"
          options={options}
          onValueChange={() => undefined}
          aria-invalid="true"
          aria-describedby="mode-error"
        />
        <p id="mode-error">Pick a supported mode.</p>
      </>,
    );
    const trigger = screen.getByRole("combobox", { name: "Mode" });
    expect(trigger).toBeInvalid();
    expect(trigger).toHaveAccessibleDescription("Pick a supported mode.");
  });

  it("shows the placeholder when nothing is chosen and can be disabled", () => {
    render(
      <>
        <Select aria-label="Mode" value="" placeholder="Choose one" options={options} onValueChange={() => undefined} />
        <Select aria-label="Locked" value="off" options={options} disabled onValueChange={() => undefined} />
      </>,
    );
    expect(screen.getByRole("combobox", { name: "Mode" })).toHaveTextContent("Choose one");
    expect(screen.getByRole("combobox", { name: "Locked" })).toBeDisabled();
  });
});

describe("CheckboxGroup", () => {
  function Harness({ onChange = () => undefined, invalid = false }: { onChange?: (v: string[]) => void; invalid?: boolean }) {
    const [value, setValue] = useState(["off"]);
    return (
      <>
        <CheckboxGroup
          legend="Triggers"
          options={options}
          value={value}
          aria-describedby="help"
          invalid={invalid}
          onValueChange={(next) => {
            setValue(next);
            onChange(next);
          }}
        />
        <p id="help">Pick any.</p>
      </>
    );
  }

  it("is a group named by its legend with one checkbox per option, checked as the value says", () => {
    render(<Harness />);
    const group = screen.getByRole("group", { name: "Triggers" });
    expect(group).toHaveAccessibleDescription("Pick any.");
    const boxes = within(group).getAllByRole("checkbox");
    expect(boxes.map((b) => [b.getAttribute("aria-checked"), b.id !== ""])).toEqual([
      ["true", true],
      ["false", true],
      ["false", true],
    ]);
    expect(within(group).getByRole("checkbox", { name: "Ask first" })).toBeInTheDocument();
  });

  it("toggles with a click on the label or Space on the box, keeping option order", async () => {
    const onChange = vi.fn();
    render(<Harness onChange={onChange} />);
    await userEvent.click(screen.getByText("Automatic"));
    expect(onChange).toHaveBeenLastCalledWith(["off", "auto"]);

    await userEvent.click(screen.getByRole("checkbox", { name: "Ask first" }));
    expect(onChange).toHaveBeenLastCalledWith(["off", "ask", "auto"]);

    screen.getByRole("checkbox", { name: "Off" }).focus();
    await userEvent.keyboard(" ");
    expect(onChange).toHaveBeenLastCalledWith(["ask", "auto"]);
  });

  it("marks every checkbox invalid when the group is", () => {
    render(<Harness invalid />);
    for (const box of screen.getAllByRole("checkbox")) expect(box).toBeInvalid();
  });
});
