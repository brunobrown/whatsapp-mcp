// Literal interface text is forbidden in components: every text comes from src/i18n/en.ts.
const message = "Interface text must come from the message catalog (t / useT), not a literal.";

export const literalTextRule = [
  "error",
  { selector: "JSXText[value=/\\S/]", message },
  { selector: "JSXElement > JSXExpressionContainer > Literal[value=/\\S/]", message },
  {
    selector: "JSXAttribute[name.name=/^(aria-label|placeholder|title|alt)$/][value.type='Literal']",
    message,
  },
];
