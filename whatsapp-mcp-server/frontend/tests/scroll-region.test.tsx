import { act, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";
import { ScrollRegion } from "../src/components/ui/scroll-region";

/** jsdom has no layout: report the sizes a browser would. */
function layout(scrollHeight: number, clientHeight: number, scrollWidth = clientHeight, clientWidth = clientHeight): void {
  Object.defineProperty(HTMLElement.prototype, "scrollHeight", { configurable: true, get: () => scrollHeight });
  Object.defineProperty(HTMLElement.prototype, "clientHeight", { configurable: true, get: () => clientHeight });
  Object.defineProperty(HTMLElement.prototype, "scrollWidth", { configurable: true, get: () => scrollWidth });
  Object.defineProperty(HTMLElement.prototype, "clientWidth", { configurable: true, get: () => clientWidth });
}

afterEach(() => {
  Reflect.deleteProperty(HTMLElement.prototype, "scrollHeight");
  Reflect.deleteProperty(HTMLElement.prototype, "clientHeight");
  Reflect.deleteProperty(HTMLElement.prototype, "scrollWidth");
  Reflect.deleteProperty(HTMLElement.prototype, "clientWidth");
});

function scrollTo(element: HTMLElement, top: number): void {
  element.scrollTop = top;
  act(() => {
    fireEvent.scroll(element);
  });
}

function scrollSideways(element: HTMLElement, left: number): void {
  element.scrollLeft = left;
  act(() => {
    fireEvent.scroll(element);
  });
}

describe("ScrollRegion", () => {
  it("UXP-12 shows a shadow only on the cut edges", () => {
    layout(1000, 300);
    render(
      <ScrollRegion label="Rows">
        <p>content</p>
      </ScrollRegion>,
    );
    const region = screen.getByRole("region", { name: "Rows" });
    // At the top: the bottom is cut, the top is not.
    expect(region).toHaveAttribute("data-top", "false");
    expect(region).toHaveAttribute("data-bottom", "true");
    expect(document.querySelector('[data-edge="bottom"]')).not.toBeNull();
    expect(document.querySelector('[data-edge="top"]')).toBeNull();

    // In the middle: both are cut.
    scrollTo(region, 200);
    expect(region).toHaveAttribute("data-top", "true");
    expect(region).toHaveAttribute("data-bottom", "true");
    expect(document.querySelector('[data-edge="top"]')).not.toBeNull();
    expect(document.querySelector('[data-edge="bottom"]')).not.toBeNull();

    // At the end: only the top is cut.
    scrollTo(region, 700);
    expect(region).toHaveAttribute("data-top", "true");
    expect(region).toHaveAttribute("data-bottom", "false");
    expect(document.querySelector('[data-edge="bottom"]')).toBeNull();
    expect(document.querySelector('[data-edge="top"]')).not.toBeNull();
  });

  it("UXP-12 shows a shadow on the left and right edges that cut a wide content", () => {
    // 300 px tall and fitting vertically, 1000 px wide in a 300 px box.
    layout(300, 300, 1000, 300);
    render(
      <ScrollRegion label="Rows">
        <p>content</p>
      </ScrollRegion>,
    );
    const region = screen.getByRole("region", { name: "Rows" });
    // At the start: the right is cut, the left is not, and nothing is cut vertically.
    expect(region).toHaveAttribute("data-left", "false");
    expect(region).toHaveAttribute("data-right", "true");
    expect(document.querySelector('[data-edge="right"]')).not.toBeNull();
    expect(document.querySelector('[data-edge="left"]')).toBeNull();
    expect(document.querySelector('[data-edge="top"]')).toBeNull();
    expect(document.querySelector('[data-edge="bottom"]')).toBeNull();

    // In the middle: both are cut.
    scrollSideways(region, 200);
    expect(region).toHaveAttribute("data-left", "true");
    expect(region).toHaveAttribute("data-right", "true");
    expect(document.querySelector('[data-edge="left"]')).not.toBeNull();
    expect(document.querySelector('[data-edge="right"]')).not.toBeNull();

    // At the end: only the left is cut.
    scrollSideways(region, 700);
    expect(region).toHaveAttribute("data-left", "true");
    expect(region).toHaveAttribute("data-right", "false");
    expect(document.querySelector('[data-edge="right"]')).toBeNull();
    expect(document.querySelector('[data-edge="left"]')).not.toBeNull();
  });

  it("UXP-12 shows no shadow when the content fits", () => {
    layout(200, 300);
    render(
      <ScrollRegion label="Rows">
        <p>content</p>
      </ScrollRegion>,
    );
    expect(document.querySelector("[data-edge]")).toBeNull();
  });

  it("UXP-10 a scrollable region is reachable by keyboard and named", async () => {
    layout(1000, 300);
    render(
      <>
        <button type="button">before</button>
        <ScrollRegion label="Send audit">
          <p>content</p>
        </ScrollRegion>
      </>,
    );
    await userEvent.tab();
    expect(screen.getByRole("button", { name: "before" })).toHaveFocus();
    await userEvent.tab();
    const region = screen.getByRole("region", { name: "Send audit" });
    expect(region).toHaveFocus();
    expect(region).toHaveAttribute("tabindex", "0");
  });

  it("UXP-10 a region whose content fits is neither focusable nor announced as a region", () => {
    layout(200, 300);
    render(
      <ScrollRegion label="Send audit">
        <p>content</p>
      </ScrollRegion>,
    );
    expect(screen.queryByRole("region")).toBeNull();
    expect(screen.getByText("content").parentElement).not.toHaveAttribute("tabindex");
  });

  it("UXP-10 keeps a sticky header above the scrolled rows", () => {
    layout(1000, 300);
    render(
      <ScrollRegion label="Rows" header={<span>Columns</span>}>
        <p>content</p>
      </ScrollRegion>,
    );
    const header = screen.getByText("Columns").parentElement;
    expect(header?.className).toContain("sticky");
    expect(header?.className).toContain("top-0");
  });

  it("UXP-10 draws a border only when asked", () => {
    layout(200, 300);
    const { rerender } = render(<ScrollRegion label="Rows">x</ScrollRegion>);
    expect(screen.getByText("x").parentElement?.className).not.toContain("border");
    rerender(
      <ScrollRegion label="Rows" bordered>
        x
      </ScrollRegion>,
    );
    expect(screen.getByText("x").parentElement?.className).toContain("border");
  });
});
