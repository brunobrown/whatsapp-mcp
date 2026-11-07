import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { Banner, EmptyState, ErrorState, Skeleton } from "../src/components/feedback/feedback";

describe("Banner", () => {
  it.each([
    ["error", "alert"],
    ["info", "status"],
    ["success", "status"],
    ["warning", "status"],
  ] as const)("a %s banner is announced with role %s", (kind, role) => {
    render(<Banner kind={kind}>Something to read</Banner>);
    expect(screen.getByRole(role)).toHaveTextContent("Something to read");
  });

  it("shows an optional title before the message", () => {
    render(
      <Banner kind="warning" title="Restart needed">
        The bridge reads this on start.
      </Banner>,
    );
    expect(screen.getByRole("status")).toHaveTextContent("Restart needed");
    expect(screen.getByRole("status")).toHaveTextContent("The bridge reads this on start.");
  });

  it("does not rely on colour alone: every kind carries an icon hidden from assistive technology", () => {
    for (const kind of ["error", "info", "success", "warning"] as const) {
      const { container, unmount } = render(<Banner kind={kind}>Text</Banner>);
      const icon = container.querySelector("svg");
      expect(icon, kind).not.toBeNull();
      expect(icon).toHaveAttribute("aria-hidden", "true");
      unmount();
    }
  });
});

describe("EmptyState", () => {
  it("states what is missing, explains it and offers the next step", async () => {
    const onAdd = vi.fn();
    render(
      <EmptyState title="No profiles yet" description="A profile tells the monitor which chats to watch.">
        <button onClick={onAdd}>Add profile</button>
      </EmptyState>,
    );
    expect(screen.getByRole("heading", { name: "No profiles yet" })).toBeInTheDocument();
    expect(screen.getByText("A profile tells the monitor which chats to watch.")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Add profile" }));
    expect(onAdd).toHaveBeenCalledTimes(1);
  });
});

describe("ErrorState", () => {
  it("is announced as an alert with the title and the message", () => {
    render(<ErrorState title="Could not load" message="The server did not answer." onRetry={() => undefined} />);
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("Could not load");
    expect(alert).toHaveTextContent("The server did not answer.");
  });

  it("calls back when retry is clicked and when it is activated by keyboard", async () => {
    const onRetry = vi.fn();
    render(<ErrorState title="Could not load" onRetry={onRetry} />);
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(onRetry).toHaveBeenCalledTimes(1);
    await userEvent.keyboard("{Enter}");
    expect(onRetry).toHaveBeenCalledTimes(2);
  });

  it("offers no retry button when there is nothing to retry", () => {
    render(<ErrorState title="Not found" />);
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });
});

describe("Skeleton", () => {
  it("is hidden from assistive technology", () => {
    const { container } = render(<Skeleton className="h-4 w-32" />);
    const bone = container.firstElementChild;
    expect(bone).toHaveAttribute("aria-hidden", "true");
    expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
    expect(screen.queryByText(/./)).not.toBeInTheDocument();
  });
});
