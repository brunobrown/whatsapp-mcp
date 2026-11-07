/**
 * The one message catalog. Every operator-facing text of the interface lives here.
 * Parameters are written {name}. A plural is a pair of keys ending in `.one` and `.other`,
 * read through the base key with a `count` parameter.
 * Adding a language means adding a catalog with the same keys.
 */
export const en = {
  "app.name": "WhatsApp MCP",
  "app.settings": "Settings",
  "common.save": "Save",
  "common.cancel": "Cancel",
  "common.close": "Close",
  "common.back": "Back",
  "common.retry": "Try again",
  "common.loading": "Loading",
  "common.none": "None",
  "error.network": "The interface lost its connection.",
  "error.timeout": "The request took too long.",
  "error.unexpected": "Something went wrong. Try again.",
  "error.invalid": "Some values were refused.",
  "auth.wait": "Too many attempts. Wait {seconds} seconds.",
  "auth.sessionEnded": "Your session ended because of inactivity.",
  "form.errorCount.one": "{count} field needs attention",
  "form.errorCount.other": "{count} fields need attention",
} as const;

export type Catalog = typeof en;
export type MessageKey = keyof Catalog;
