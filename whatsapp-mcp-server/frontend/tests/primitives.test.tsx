import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { Badge } from "../src/components/ui/badge";
import { Button } from "../src/components/ui/button";
import { Input } from "../src/components/ui/input";

describe("Button", () => {
  it("renders as a button named by its label", () => {
    render(<Button>Save</Button>);
    expect(screen.getByRole("button", { name: "Save" })).toBeInTheDocument();
  });

  it("does not submit a form unless it says so", async () => {
    const onSubmit = vi.fn((event: React.SubmitEvent) => {
      event.preventDefault();
    });
    render(
      <form onSubmit={onSubmit}>
        <Button>Plain</Button>
        <Button type="submit">Send</Button>
      </form>,
    );
    await userEvent.click(screen.getByRole("button", { name: "Plain" }));
    expect(onSubmit).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "Send" }));
    expect(onSubmit).toHaveBeenCalledTimes(1);
  });

  it("activates with Enter and with Space", async () => {
    const onClick = vi.fn();
    render(<Button onClick={onClick}>Go</Button>);
    await userEvent.tab();
    expect(screen.getByRole("button", { name: "Go" })).toHaveFocus();
    await userEvent.keyboard("{Enter}");
    await userEvent.keyboard(" ");
    expect(onClick).toHaveBeenCalledTimes(2);
  });

  it("announces a disabled button as disabled and does not activate it", async () => {
    const onClick = vi.fn();
    render(
      <Button disabled onClick={onClick}>
        Save
      </Button>,
    );
    const button = screen.getByRole("button", { name: "Save" });
    expect(button).toBeDisabled();
    await userEvent.click(button);
    expect(onClick).not.toHaveBeenCalled();
  });

  it("announces a busy button as busy and disabled, keeps its name and ignores clicks", async () => {
    const onClick = vi.fn();
    render(
      <Button busy onClick={onClick}>
        Save
      </Button>,
    );
    const button = screen.getByRole("button", { name: "Save" });
    expect(button).toHaveAttribute("aria-busy", "true");
    expect(button).toHaveAttribute("aria-disabled", "true");
    await userEvent.click(button);
    await userEvent.type(button, "{Enter}");
    expect(onClick).not.toHaveBeenCalled();
  });

  it("is not marked busy when it is idle", () => {
    render(<Button>Save</Button>);
    expect(screen.getByRole("button", { name: "Save" })).not.toHaveAttribute("aria-busy");
  });

  it("names an icon-only button through aria-label and hides the icon from assistive technology", () => {
    render(
      <Button size="icon" aria-label="Close">
        <svg data-testid="icon" aria-hidden="true" />
      </Button>,
    );
    expect(screen.getByRole("button", { name: "Close" })).toBeInTheDocument();
    expect(screen.getByTestId("icon")).toHaveAttribute("aria-hidden", "true");
  });

  it("can render its child, such as a link, with the button look", () => {
    render(
      <Button asChild>
        <a href="/status">Status</a>
      </Button>,
    );
    expect(screen.getByRole("link", { name: "Status" })).toHaveAttribute("href", "/status");
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });
});

describe("Input", () => {
  it("renders a text box named by its label and takes typing", async () => {
    render(
      <>
        <label htmlFor="port">Port</label>
        <Input id="port" />
      </>,
    );
    const input = screen.getByRole("textbox", { name: "Port" });
    await userEvent.type(input, "8765");
    expect(input).toHaveValue("8765");
  });

  it("passes invalid and described-by through to the element", () => {
    render(<Input aria-label="Port" aria-invalid="true" aria-describedby="port-error" />);
    const input = screen.getByRole("textbox", { name: "Port" });
    expect(input).toBeInvalid();
    expect(input).toHaveAttribute("aria-describedby", "port-error");
  });

  it("announces disabled and read-only states", () => {
    render(
      <>
        <Input aria-label="Locked" readOnly />
        <Input aria-label="Off" disabled />
      </>,
    );
    expect(screen.getByRole("textbox", { name: "Locked" })).toHaveAttribute("readonly");
    expect(screen.getByRole("textbox", { name: "Off" })).toBeDisabled();
  });

  it("keeps a password field a password field", () => {
    render(<Input aria-label="Password" type="password" autoComplete="current-password" />);
    const input = screen.getByLabelText("Password");
    expect(input).toHaveAttribute("type", "password");
    expect(input).toHaveAttribute("autocomplete", "current-password");
  });

  it("hands its element to a ref", () => {
    const ref = { current: null as HTMLInputElement | null };
    render(<Input aria-label="Port" ref={ref} />);
    expect(ref.current).toBe(screen.getByRole("textbox"));
  });
});

describe("Badge", () => {
  it("shows its label", () => {
    render(<Badge>Restart needed</Badge>);
    expect(screen.getByText("Restart needed")).toBeInTheDocument();
  });

  it("exposes a status role when asked to", () => {
    render(<Badge role="status">Connected</Badge>);
    expect(screen.getByRole("status")).toHaveTextContent("Connected");
  });

  it.each(["neutral", "accent", "warning", "danger"] as const)("renders the %s variant with its text", (variant) => {
    render(<Badge variant={variant}>Label</Badge>);
    expect(screen.getByText("Label")).toHaveAttribute("data-variant", variant);
  });
});
