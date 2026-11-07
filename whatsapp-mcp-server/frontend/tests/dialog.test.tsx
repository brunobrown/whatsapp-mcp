import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import { ConfirmDialog } from "../src/components/ui/dialog";
import { Dialog, DialogContent, DialogDescription, DialogTitle, DialogTrigger } from "../src/components/ui/dialog";

describe("Dialog", () => {
  function Plain() {
    return (
      <Dialog>
        <DialogTrigger asChild>
          <button>Open details</button>
        </DialogTrigger>
        <DialogContent>
          <DialogTitle>Details</DialogTitle>
          <DialogDescription>More about this section.</DialogDescription>
          <input aria-label="Name" />
        </DialogContent>
      </Dialog>
    );
  }

  it("opens as a named, described dialog", async () => {
    render(<Plain />);
    await userEvent.click(screen.getByRole("button", { name: "Open details" }));
    const dialog = await screen.findByRole("dialog", { name: "Details" });
    expect(dialog).toHaveAccessibleDescription("More about this section.");
  });

  it("traps Tab inside the dialog", async () => {
    render(<Plain />);
    await userEvent.click(screen.getByRole("button", { name: "Open details" }));
    const dialog = await screen.findByRole("dialog");
    for (let step = 0; step < 6; step += 1) {
      await userEvent.tab();
      expect(dialog).toContainElement(document.activeElement as HTMLElement);
    }
  });

  it("closes with Escape and returns focus to the trigger", async () => {
    render(<Plain />);
    const trigger = screen.getByRole("button", { name: "Open details" });
    await userEvent.click(trigger);
    await screen.findByRole("dialog");
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
  });

  it("closes from its close button, which is named from the catalog", async () => {
    render(<Plain />);
    await userEvent.click(screen.getByRole("button", { name: "Open details" }));
    await userEvent.click(await screen.findByRole("button", { name: "Close" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});

describe("ConfirmDialog", () => {
  function Harness(props: {
    onConfirm?: () => void;
    onCancel?: () => void;
    destructive?: boolean;
  }) {
    const [open, setOpen] = useState(false);
    const common = {
      open,
      onOpenChange: (next: boolean) => {
        setOpen(next);
        if (!next) props.onCancel?.();
      },
      title: "Replace file",
      description: "The file will be rewritten.",
      confirmLabel: "Replace",
      cancelLabel: "Keep",
      onConfirm: () => props.onConfirm?.(),
    };
    return (
      <>
        <button
          onClick={() => {
            setOpen(true);
          }}
        >
          Ask
        </button>
        {props.destructive ? (
          <ConfirmDialog {...common} destructive consequence="Every saved profile is lost." />
        ) : (
          <ConfirmDialog {...common} />
        )}
      </>
    );
  }

  it("is an alert dialog with a title, a description and two named actions", async () => {
    render(<Harness />);
    await userEvent.click(screen.getByRole("button", { name: "Ask" }));
    const dialog = await screen.findByRole("alertdialog", { name: "Replace file" });
    expect(dialog).toHaveAccessibleDescription("The file will be rewritten.");
    expect(screen.getByRole("button", { name: "Replace" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Keep" })).toBeInTheDocument();
  });

  it("puts focus on the confirm action, so Enter confirms", async () => {
    const onConfirm = vi.fn();
    render(<Harness onConfirm={onConfirm} />);
    await userEvent.click(screen.getByRole("button", { name: "Ask" }));
    await screen.findByRole("alertdialog");
    expect(screen.getByRole("button", { name: "Replace" })).toHaveFocus();
    await userEvent.keyboard("{Enter}");
    expect(onConfirm).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
  });

  it("cancels with Escape, without confirming, and returns focus to what opened it", async () => {
    const onConfirm = vi.fn();
    const onCancel = vi.fn();
    render(<Harness onConfirm={onConfirm} onCancel={onCancel} />);
    const opener = screen.getByRole("button", { name: "Ask" });
    await userEvent.click(opener);
    await screen.findByRole("alertdialog");
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    expect(onConfirm).not.toHaveBeenCalled();
    expect(onCancel).toHaveBeenCalledTimes(1);
    expect(opener).toHaveFocus();
  });

  it("cancels from the cancel action", async () => {
    const onConfirm = vi.fn();
    render(<Harness onConfirm={onConfirm} />);
    await userEvent.click(screen.getByRole("button", { name: "Ask" }));
    await userEvent.click(await screen.findByRole("button", { name: "Keep" }));
    expect(onConfirm).not.toHaveBeenCalled();
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
  });

  it("keeps focus inside while open", async () => {
    render(<Harness />);
    await userEvent.click(screen.getByRole("button", { name: "Ask" }));
    const dialog = await screen.findByRole("alertdialog");
    for (let step = 0; step < 5; step += 1) {
      await userEvent.tab();
      expect(dialog).toContainElement(document.activeElement as HTMLElement);
    }
  });

  describe("destructive", () => {
    it("names the consequence in the description", async () => {
      render(<Harness destructive />);
      await userEvent.click(screen.getByRole("button", { name: "Ask" }));
      const dialog = await screen.findByRole("alertdialog");
      expect(dialog).toHaveAccessibleDescription(/The file will be rewritten\..*Every saved profile is lost\./);
    });

    it("starts on the safe action, so Enter alone does not confirm", async () => {
      const onConfirm = vi.fn();
      render(<Harness destructive onConfirm={onConfirm} />);
      await userEvent.click(screen.getByRole("button", { name: "Ask" }));
      await screen.findByRole("alertdialog");
      expect(screen.getByRole("button", { name: "Keep" })).toHaveFocus();
      await userEvent.keyboard("{Enter}");
      expect(onConfirm).not.toHaveBeenCalled();
    });

    it("confirms when the operator moves to the destructive action and presses Enter", async () => {
      const onConfirm = vi.fn();
      render(<Harness destructive onConfirm={onConfirm} />);
      await userEvent.click(screen.getByRole("button", { name: "Ask" }));
      await screen.findByRole("alertdialog");
      await userEvent.tab();
      expect(screen.getByRole("button", { name: "Replace" })).toHaveFocus();
      await userEvent.keyboard("{Enter}");
      expect(onConfirm).toHaveBeenCalledTimes(1);
    });
  });
});
