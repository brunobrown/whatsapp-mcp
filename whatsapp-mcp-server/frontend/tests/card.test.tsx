import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Card, CardBody, CardHeader } from "../src/components/ui/card";

describe("Card", () => {
  it("UXP-14 a card has a heading, a border and its own surface", () => {
    render(
      <Card>
        <CardHeader title="Timing" description="How fast notices go out." />
        <CardBody>
          <p>body</p>
        </CardBody>
      </Card>,
    );
    const card = screen.getByRole("region", { name: "Timing" });
    expect(within(card).getByRole("heading", { level: 2, name: "Timing" })).toBeInTheDocument();
    expect(within(card).getByText("How fast notices go out.")).toBeInTheDocument();
    expect(card).toHaveClass("border", "rounded-card");
    // The surface is the panel colour; the page is the surface colour.
    expect(card).toHaveClass("bg-panel");
    expect(card).not.toHaveClass("bg-surface");
    expect(within(card).getByText("body")).toBeInTheDocument();
  });

  it("UXP-14 the heading level is chosen by the caller and the description is optional", () => {
    render(
      <Card>
        <CardHeader title="Inner" level={3} />
      </Card>,
    );
    expect(screen.getByRole("heading", { level: 3, name: "Inner" })).toBeInTheDocument();
    expect(document.querySelector("p")).toBeNull();
  });

  it("UXP-19 an invalid card is marked needs attention", () => {
    render(
      <>
        <Card>
          <CardHeader title="Timing" error />
        </Card>
        <Card>
          <CardHeader title="Excerpt" />
        </Card>
      </>,
    );
    const heading = screen.getByRole("heading", { name: /Timing/ });
    expect(heading).toHaveTextContent("needs attention");
    expect(heading.querySelector("svg")).toHaveAttribute("aria-hidden", "true");
    expect(screen.getByRole("heading", { name: "Excerpt" })).not.toHaveTextContent("needs attention");
  });

  it("UXP-19 two cards keep their own titles", () => {
    render(
      <>
        <Card>
          <CardHeader title="One" />
        </Card>
        <Card>
          <CardHeader title="Two" />
        </Card>
      </>,
    );
    expect(screen.getByRole("region", { name: "One" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Two" })).toBeInTheDocument();
  });
});
