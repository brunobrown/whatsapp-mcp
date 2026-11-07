import { en } from "./en";
import type { MessageKey } from "./en";

type PluralSuffix = ".one" | ".other";
type PluralBase = Extract<MessageKey, `${string}${PluralSuffix}`> extends `${infer Base}${PluralSuffix}` ? Base : never;
/** Keys a caller may pass: plain keys, and the base of a `.one`/`.other` pair. */
export type TextKey = Exclude<MessageKey, `${string}${PluralSuffix}`> | PluralBase;

export type TextParams = Readonly<Record<string, string | number>>;

const messages: Readonly<Record<string, string>> = en;
const plurals = new Intl.PluralRules("en");

function interpolate(template: string, params: TextParams | undefined): string {
  if (!params) return template;
  return template.replace(/\{(\w+)\}/g, (placeholder, name: string) => {
    const value = params[name];
    return value === undefined ? placeholder : String(value);
  });
}

function resolve(key: string, params: TextParams | undefined): string | undefined {
  const direct = messages[key];
  if (direct !== undefined) return direct;
  const count = params?.["count"];
  if (count === undefined) return undefined;
  const form = plurals.select(Number(count)) === "one" ? "one" : "other";
  return messages[`${key}.${form}`];
}

/** Looks a key up in the catalog, fills {name} parameters and picks the plural form from `count`. */
export function t(key: TextKey, params?: TextParams): string {
  const template = resolve(key, params);
  return template === undefined ? key : interpolate(template, params);
}

/** Hook form of `t`, so a component reads its texts the same way once a second language exists. */
export function useT(): typeof t {
  return t;
}
