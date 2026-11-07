// Literal interface text is forbidden in components: every text comes from src/i18n/en.ts.
const message = "Interface text must come from the message catalog (t / useT), not a literal.";

const WORDS = "/[A-Za-zÀ-ÿ]/";
const TEXT_PROPS =
  "/^(aria-label|aria-description|aria-placeholder|aria-roledescription|aria-valuetext|placeholder|title|alt|label|description|hint|helper|helperText|caption|legend|summary|heading|subtitle|tooltip|text|message|error|emptyText)$/";
const literalWithWords = `Literal[value=${WORDS}]`;

/** Selectors for the places a literal can flow into a container (a child expression or a text prop). */
function flows(container) {
  return [
    `${container} > ${literalWithWords}`,
    `${container} > ConditionalExpression > ${literalWithWords}`,
    `${container} > ConditionalExpression > ConditionalExpression > ${literalWithWords}`,
    `${container} > LogicalExpression > ${literalWithWords}`,
    `${container} > BinaryExpression[operator='+'] > ${literalWithWords}`,
    `${container} > TemplateLiteral > TemplateElement[value.raw=${WORDS}]`,
    `${container} > ConditionalExpression > TemplateLiteral > TemplateElement[value.raw=${WORDS}]`,
  ];
}

export const literalTextRule = [
  "error",
  { selector: "JSXText[value=/\\S/]", message },
  ...flows("JSXElement > JSXExpressionContainer").map((selector) => ({ selector, message })),
  ...flows("JSXFragment > JSXExpressionContainer").map((selector) => ({ selector, message })),
  { selector: `JSXAttribute[name.name=${TEXT_PROPS}][value.type='Literal']`, message },
  ...flows(`JSXAttribute[name.name=${TEXT_PROPS}] > JSXExpressionContainer`).map((selector) => ({ selector, message })),
];
