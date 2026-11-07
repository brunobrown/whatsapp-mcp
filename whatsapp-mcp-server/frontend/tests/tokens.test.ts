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
});
