// @vitest-environment node
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const css = readFileSync("src/index.css", "utf8");

/** `--name: light-dark(a, b)` pairs of the :root block. */
function tokens(): Map<string, { light: string; dark: string }> {
  const found = new Map<string, { light: string; dark: string }>();
  for (const match of css.matchAll(/--([\w-]+):\s*light-dark\(\s*(#[0-9a-fA-F]{6})\s*,\s*(#[0-9a-fA-F]{6})\s*\)/g)) {
    const [, name, light, dark] = match;
    if (name && light && dark) found.set(name, { light: light.toLowerCase(), dark: dark.toLowerCase() });
  }
  return found;
}

function luminance(hex: string): number {
  const channel = (offset: number): number => {
    const value = parseInt(hex.slice(offset, offset + 2), 16) / 255;
    return value <= 0.03928 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * channel(1) + 0.7152 * channel(3) + 0.0722 * channel(5);
}

function contrast(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x) as [number, number];
  return (hi + 0.05) / (lo + 0.05);
}

describe("design tokens", () => {
  it.each(["surface", "panel", "ink", "muted", "line"])("defines %s differently for the light and the dark theme", (name) => {
    const token = tokens().get(name);
    expect(token, `--${name}`).toBeDefined();
    expect(token?.light).not.toBe(token?.dark);
  });

  it("uses the green accent family of #00a884 in both themes", () => {
    expect(tokens().get("accent")).toEqual({ light: "#00a884", dark: "#00a884" });
  });

  it("holds the light and dark palettes of the design", () => {
    const t = tokens();
    expect(t.get("surface")).toEqual({ light: "#f0f2f5", dark: "#111b21" });
    expect(t.get("panel")).toEqual({ light: "#ffffff", dark: "#202c33" });
    expect(t.get("ink")).toEqual({ light: "#111b21", dark: "#e9edef" });
    expect(t.get("line")).toEqual({ light: "#e9edef", dark: "#2a3942" });
  });

  const textPairs: [string, string][] = [
    ["ink", "surface"],
    ["ink", "panel"],
    ["ink", "hover"],
    ["ink", "selected"],
    ["muted", "surface"],
    ["muted", "panel"],
    ["muted", "hover"],
    ["muted", "selected"],
    ["accent-ink", "surface"],
    ["accent-ink", "panel"],
    ["accent-ink", "hover"],
    ["accent-ink", "selected"],
    ["accent-ink", "accent-soft"],
    ["on-accent", "accent-solid"],
    ["on-accent", "accent-solid-hover"],
    ["danger-ink", "panel"],
    ["danger-ink", "danger-soft"],
    ["warning-ink", "panel"],
    ["warning-ink", "warning-soft"],
  ];
  it.each(textPairs)("keeps %s readable on %s at 4.5:1 or more in both themes", (foreground, background) => {
    const fg = tokens().get(foreground);
    const bg = tokens().get(background);
    expect(fg, `--${foreground}`).toBeDefined();
    expect(bg, `--${background}`).toBeDefined();
    expect(contrast(fg?.light ?? "", bg?.light ?? "")).toBeGreaterThanOrEqual(4.5);
    expect(contrast(fg?.dark ?? "", bg?.dark ?? "")).toBeGreaterThanOrEqual(4.5);
  });

  it("follows the system theme until a theme is forced", () => {
    expect(css).toMatch(/:root\s*{[^}]*color-scheme:\s*light dark/);
    expect(css).toMatch(/:root\[data-theme="light"\]\s*{[^}]*color-scheme:\s*light/);
    expect(css).toMatch(/:root\[data-theme="dark"\]\s*{[^}]*color-scheme:\s*dark/);
  });

  it("draws a 2 px focus ring and respects reduced motion", () => {
    expect(css).toMatch(/:focus-visible\s*{[^}]*outline:\s*2px solid/);
    expect(css).toMatch(/@media \(prefers-reduced-motion: reduce\)/);
  });

  it("loads no webfont and sets the system font stack", () => {
    const html = readFileSync("index.html", "utf8");
    expect(css).not.toMatch(/@font-face|fonts\.googleapis|fonts\.gstatic|@import url/);
    expect(html).not.toMatch(/fonts\.googleapis|fonts\.gstatic|<link[^>]+font/i);
    expect(css).toMatch(/--font-sans:[^;]*"Segoe UI"[^;]*"Helvetica Neue"[^;]*Arial/);
  });

  /** The body of the first block whose header matches `header` (a regex source), braces balanced. */
  function block(header: string): string {
    const start = css.search(new RegExp(header));
    expect(start, header).toBeGreaterThanOrEqual(0);
    const open = css.indexOf("{", start);
    let depth = 0;
    for (let i = open; i < css.length; i++) {
      if (css[i] === "{") depth++;
      if (css[i] === "}" && --depth === 0) return css.slice(open + 1, i);
    }
    throw new Error("unbalanced " + header);
  }

  it("UXP-43 the viewport meta allows zoom", () => {
    const html = readFileSync("index.html", "utf8");
    const meta = /<meta\s+name="viewport"\s+content="([^"]*)"/.exec(html)?.[1] ?? "";
    expect(meta).toContain("width=device-width");
    expect(meta).not.toMatch(/user-scalable\s*=\s*(no|0)/i);
    expect(meta).not.toMatch(/maximum-scale/i);
    expect(meta).toContain("interactive-widget=resizes-content");
    expect(meta).toContain("viewport-fit=cover");
  });

  it("UXP-33 reduced motion plays no transition or animation longer than 1 ms", () => {
    const body = block("@media \\(prefers-reduced-motion: reduce\\)");
    for (const property of ["animation-duration", "transition-duration"]) {
      const value = new RegExp(property + ":\\s*([\\d.]+)ms").exec(body)?.[1];
      expect(Number(value), property).toBeLessThanOrEqual(1);
    }
    expect(body).toMatch(/scroll-behavior:\s*auto/);
  });

  it("UXP-40 the shell uses the dynamic viewport height and the four safe-area insets", () => {
    expect(css).toMatch(/100dvh/);
    for (const side of ["top", "right", "bottom", "left"]) {
      expect(css).toMatch(new RegExp("--safe-" + side + ":\\s*env\\(safe-area-inset-" + side));
    }
    expect(css).toMatch(/padding-(top|block)[^;]*var\(--safe-top\)|--edge-top/);
  });

  it("UXP-36 text fields are 16 px or larger under 600 px", () => {
    const body = block("@media \\(max-width: 599\\.98px\\)");
    expect(body).toMatch(/input[\s\S]*select[\s\S]*textarea[\s\S]*{[^}]*font-size:\s*16px/);
  });

  it("UXP-35 controls have a 44 px hit area under 600 px", () => {
    expect(css).toMatch(/--min-target:\s*2\.75rem/);
    const body = block("@media \\(max-width: 599\\.98px\\)");
    for (const selector of ["button", "a[href]", '[role="switch"]', '[role="radio"]', '[role="checkbox"]', "summary"]) {
      expect(body, selector).toContain(selector);
    }
    expect(body).toMatch(/min-height:\s*var\(--min-target\)/);
    expect(body).toMatch(/min-width:\s*var\(--min-target\)/);
  });
});
