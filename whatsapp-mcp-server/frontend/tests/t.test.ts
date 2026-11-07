import { describe, expect, it } from "vitest";
import { en } from "../src/i18n/en";
import { t, useT } from "../src/i18n/t";

describe("t", () => {
  it("returns the catalog text for a plain key", () => {
    expect(t("common.save")).toBe(en["common.save"]);
  });

  it("fills {name} parameters, every occurrence", () => {
    expect(t("auth.wait", { seconds: 12 })).toBe("Too many attempts. Wait 12 seconds.");
  });

  it("picks the singular form for a count of one", () => {
    expect(t("form.errorCount", { count: 1 })).toBe("1 field needs attention");
  });

  it("picks the plural form for any other count, zero included", () => {
    expect(t("form.errorCount", { count: 3 })).toBe("3 fields need attention");
    expect(t("form.errorCount", { count: 0 })).toBe("0 fields need attention");
  });

  it("leaves an unknown placeholder visible instead of printing undefined", () => {
    expect(t("auth.wait", {})).toContain("{seconds}");
  });

  it("rejects a key that is not in the catalog at compile time", () => {
    // @ts-expect-error the key does not exist in the catalog
    const text = t("no.such.key");
    expect(text).toBe("no.such.key");
  });

  it("is available to components through useT", () => {
    expect(useT()("common.cancel")).toBe(en["common.cancel"]);
  });
});
