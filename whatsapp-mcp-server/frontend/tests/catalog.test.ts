// @vitest-environment node
import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { Linter } from "eslint";
import tseslint from "typescript-eslint";
import { describe, expect, it } from "vitest";
import { literalTextRule } from "../eslint.literals";
import { findLiteralText } from "./helpers/literalText";

function tsxFiles(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) return tsxFiles(path);
    return entry.name.endsWith(".tsx") ? [path] : [];
  });
}

describe("catalog", () => {
  it("components contain no literal interface text", () => {
    const offenders = tsxFiles("src").flatMap((file) =>
      findLiteralText(readFileSync(file, "utf8"), file).map((f) => `${file}:${String(f.line)} "${f.text}"`),
    );
    expect(offenders).toEqual([]);
  });

  it("the scanner flags literal text, string children and literal text attributes", () => {
    const source = [
      "export const A = () => (",
      '  <div title="Hint">',
      "    Hello",
      '    <input placeholder="Type" aria-label="Name" />',
      '    {"Literal"}',
      "  </div>",
      ");",
    ].join("\n");
    expect(findLiteralText(source).map((f) => f.text)).toEqual(["Hint", "Hello", "Type", "Name", "Literal"]);
  });

  it("the scanner accepts text that comes from the catalog", () => {
    const source = 'export const A = () => <button aria-label={t("common.close")}>{t("common.save")}</button>;';
    expect(findLiteralText(source)).toEqual([]);
  });
});

const FLAGGED: Array<[string, string, number]> = [
  ["a conditional in children", 'const a = <p>{ok ? "On" : "Off"}</p>;', 2],
  ["a conditional with one catalog branch", 'const a = <p>{ok ? t("a.b") : "Off"}</p>;', 1],
  ["a template literal in children", "const a = <p>{`Item ${n}`}</p>;", 1],
  ["a logical fallback in children", 'const a = <p>{name || "Unknown"}</p>;', 1],
  ["a concatenation in children", 'const a = <p>{count + " items"}</p>;', 1],
  ["a label prop", 'const a = <Field label="Name" />;', 1],
  ["a description prop", 'const a = <Field description="Some help" />;', 1],
  ["an aria-description prop", 'const a = <p aria-description="Help" />;', 1],
  ["a conditional text prop", 'const a = <Field label={ok ? "On" : "Off"} />;', 2],
  ["a template text prop", "const a = <Field label={`Item ${n}`} />;", 1],
  ["a literal in a braced text prop", 'const a = <Field label={"Name"} />;', 1],
];
const ACCEPTED: Array<[string, string]> = [
  ["catalog text everywhere", 'const a = <Field label={t("a.b")} title={ok ? t("a.c") : t("a.d")}>{ok ? t("a.e") : t("a.f")}</Field>;'],
  ["punctuation and spacing", 'const a = <p>{a}{", "}{b}{" "}{`${a}/${b}`}</p>;'],
  ["a comparison and a key", 'const a = <p key="row-1" className="x">{status === "ready" ? t("a.b") : t("a.c")}</p>;'],
  ["a number or a variable", "const a = <p>{count}{name ?? other}</p>;"],
];

describe("literal scanner (every pattern)", () => {
  it.each(FLAGGED)("flags %s", (_name, source, count) => {
    expect(findLiteralText(source)).toHaveLength(count);
  });
  it.each(ACCEPTED)("accepts %s", (_name, source) => {
    expect(findLiteralText(source)).toEqual([]);
  });
});

describe("eslint literal text rule", () => {
  const linter = new Linter({ configType: "flat" });
  const lint = (code: string) =>
    linter.verify(code, [
      {
        files: ["**/*.tsx"],
        languageOptions: { parser: tseslint.parser, parserOptions: { ecmaFeatures: { jsx: true } } },
        rules: { "no-restricted-syntax": literalTextRule },
      },
    ], "probe.tsx");

  it("rejects literal JSX text and literal aria-label, placeholder and title", () => {
    expect(lint("const a = <p>Hello</p>;")).toHaveLength(1);
    expect(lint('const a = <p aria-label="x" />;')).toHaveLength(1);
    expect(lint('const a = <input placeholder="x" />;')).toHaveLength(1);
    expect(lint('const a = <p title="x" />;')).toHaveLength(1);
  });

  it.each(FLAGGED)("rejects %s", (_name, source, count) => {
    expect(lint(source)).toHaveLength(count);
  });

  it.each(ACCEPTED)("accepts %s", (_name, source) => {
    expect(lint(source)).toHaveLength(0);
  });

  it("accepts text read from the catalog", () => {
    expect(lint('const a = <p title={t("a.b")}>{t("a.c")}</p>;')).toHaveLength(0);
  });
});

describe("index.html", () => {
  it("declares lang en", () => {
    const html = readFileSync("index.html", "utf8");
    expect(html).toMatch(/<html[^>]*\slang="en"/);
  });
});
