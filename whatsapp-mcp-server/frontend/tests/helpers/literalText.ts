import ts from "typescript";

const LITERAL_ATTRIBUTES = new Set(["aria-label", "placeholder", "title", "alt"]);

export interface LiteralFinding {
  line: number;
  text: string;
}

/** Finds literal interface text in a TSX source: JSX text, string children and literal text attributes. */
export function findLiteralText(source: string, fileName = "probe.tsx"): LiteralFinding[] {
  const file = ts.createSourceFile(fileName, source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  const findings: LiteralFinding[] = [];
  const report = (node: ts.Node, text: string): void => {
    const { line } = file.getLineAndCharacterOfPosition(node.getStart(file));
    findings.push({ line: line + 1, text: text.trim() });
  };
  const visit = (node: ts.Node): void => {
    if (ts.isJsxText(node) && /\S/.test(node.text)) {
      report(node, node.text);
    } else if (
      ts.isJsxExpression(node) &&
      (ts.isJsxElement(node.parent) || ts.isJsxFragment(node.parent)) &&
      node.expression &&
      (ts.isStringLiteral(node.expression) || ts.isNoSubstitutionTemplateLiteral(node.expression)) &&
      /\S/.test(node.expression.text)
    ) {
      report(node, node.expression.text);
    } else if (ts.isJsxAttribute(node) && LITERAL_ATTRIBUTES.has(node.name.getText(file)) && node.initializer) {
      const init = node.initializer;
      const literal = ts.isStringLiteral(init)
        ? init
        : ts.isJsxExpression(init) && init.expression && ts.isStringLiteral(init.expression)
          ? init.expression
          : null;
      if (literal && /\S/.test(literal.text)) report(node, literal.text);
    }
    ts.forEachChild(node, visit);
  };
  visit(file);
  return findings;
}
