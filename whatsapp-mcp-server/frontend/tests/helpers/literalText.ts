import ts from "typescript";

/** Props that carry text a person reads (or a screen reader announces). Their value must come from the catalog. */
export const TEXT_PROPS = new Set([
  "aria-label",
  "aria-description",
  "aria-placeholder",
  "aria-roledescription",
  "aria-valuetext",
  "placeholder",
  "title",
  "alt",
  "label",
  "description",
  "hint",
  "helper",
  "helperText",
  "caption",
  "legend",
  "summary",
  "heading",
  "subtitle",
  "tooltip",
  "text",
  "message",
  "error",
  "emptyText",
]);

export interface LiteralFinding {
  line: number;
  text: string;
}

const HAS_LETTER = /[A-Za-zÀ-ÿ]/;

/** Whether a string-ish literal node carries words (punctuation such as ", " or "/" is not interface text). */
function wordsOf(node: ts.Node): string | null {
  if (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node)) return HAS_LETTER.test(node.text) ? node.text : null;
  if (ts.isTemplateExpression(node)) {
    const parts = [node.head.text, ...node.templateSpans.map((span) => span.literal.text)];
    const joined = parts.join("");
    return HAS_LETTER.test(joined) ? joined : null;
  }
  return null;
}

/**
 * The literals whose text can reach the screen from a JSX expression: the expression itself, the branches of a
 * conditional, the right side of && || ??, the operands of +, a parenthesised or cast expression, and a template with
 * words in it. Calls (t("key") included), comparisons and property keys are not followed: a string there is a key or a
 * value that is compared, not text that is shown.
 */
function shownLiterals(expression: ts.Expression): Array<{ node: ts.Node; text: string }> {
  const found: Array<{ node: ts.Node; text: string }> = [];
  const visit = (node: ts.Expression): void => {
    const words = wordsOf(node);
    if (words !== null) {
      found.push({ node, text: words });
    } else if (ts.isConditionalExpression(node)) {
      visit(node.whenTrue);
      visit(node.whenFalse);
    } else if (ts.isParenthesizedExpression(node) || ts.isAsExpression(node) || ts.isNonNullExpression(node)) {
      visit(node.expression);
    } else if (ts.isBinaryExpression(node)) {
      const op = node.operatorToken.kind;
      if (op === ts.SyntaxKind.PlusToken) {
        visit(node.left);
        visit(node.right);
      } else if (
        op === ts.SyntaxKind.AmpersandAmpersandToken ||
        op === ts.SyntaxKind.BarBarToken ||
        op === ts.SyntaxKind.QuestionQuestionToken
      ) {
        visit(node.right);
        if (op !== ts.SyntaxKind.AmpersandAmpersandToken) visit(node.left);
      }
    }
  };
  visit(expression);
  return found;
}

/** Finds literal interface text in a TSX source: JSX text, literals that flow into children, and literal text props. */
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
      node.expression
    ) {
      for (const literal of shownLiterals(node.expression)) report(literal.node, literal.text);
    } else if (ts.isJsxAttribute(node) && TEXT_PROPS.has(node.name.getText(file)) && node.initializer) {
      const init = node.initializer;
      if (ts.isStringLiteral(init)) {
        if (/\S/.test(init.text)) report(node, init.text);
      } else if (ts.isJsxExpression(init) && init.expression) {
        for (const literal of shownLiterals(init.expression)) report(literal.node, literal.text);
      }
    }
    ts.forEachChild(node, visit);
  };
  visit(file);
  return findings;
}
