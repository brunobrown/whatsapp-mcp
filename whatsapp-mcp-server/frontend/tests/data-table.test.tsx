// @vitest-environment jsdom
import { readFileSync } from "node:fs";
import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { Cell, DataTable } from "../src/features/ops/DataTable";
import { mockOverflow } from "./helpers/overflow";

const css = readFileSync("src/index.css", "utf8");

let overflow: ReturnType<typeof mockOverflow> | undefined;
afterEach(() => {
  overflow?.restore();
  overflow = undefined;
});

function longTable() {
  render(
    <DataTable label="Rows" columns={["Name", "Count"]}>
      {Array.from({ length: 40 }, (_, i) => (
        <tr key={i}>
          <Cell>{`row ${String(i)}`}</Cell>
          <Cell>{i}</Cell>
        </tr>
      ))}
    </DataTable>,
  );
}

describe("DataTable", () => {
  it("UXP-10 a long table scrolls inside its region with a sticky header", () => {
    overflow = mockOverflow();
    longTable();
    const region = screen.getByRole("region", { name: "Rows" });
    expect(region).toHaveAttribute("tabindex", "0");
    expect(region.className).toContain("max-h-[24rem]");
    expect(region.className).toContain("overflow-auto");
    expect(region).toHaveAttribute("data-bottom", "true");
    expect(document.querySelector('[data-edge="bottom"]')).not.toBeNull();
    // The header row is a real header of the table and the stylesheet pins it to the top of the box.
    expect(screen.getAllByRole("columnheader").map((h) => h.textContent)).toEqual(["Name", "Count"]);
    expect(css).toMatch(/\[data-table\] thead th\s*{[^}]*position:\s*sticky;[^}]*top:\s*0/);
    expect(document.querySelector("table")).toHaveAttribute("data-table");
  });

  it("UXP-10 a table that fits is not a tab stop", () => {
    longTable();
    expect(screen.queryByRole("region", { name: "Rows" })).toBeNull();
  });

  it("UXP-38 first column stays under 600 px", () => {
    const start = css.search(/@media \(max-width: 599\.98px\)\s*{\s*\[data-table\] th:first-child/);
    expect(start).toBeGreaterThanOrEqual(0);
    const rule = /{([^}]*)}/.exec(css.slice(css.indexOf("{", css.indexOf("[data-table] th:first-child", start))))?.[1] ?? "";
    expect(rule).toMatch(/position:\s*sticky/);
    expect(rule).toMatch(/left:\s*0/);
    longTable();
    expect(document.querySelector("table")).toHaveAttribute("data-table");
  });
});
