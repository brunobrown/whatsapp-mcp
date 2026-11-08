import { describe, expect, it } from "vitest";
import { groupFields } from "../src/features/sections/groupFields";
import type { FieldSchema } from "../src/types/api";

function f(key: string, overrides: Partial<FieldSchema> = {}): FieldSchema {
  return {
    key, label: key.toUpperCase(), kind: "text", default: null, help: "", secret: false, env_var: null, options: [], bounds: null,
    max_length: 1000, max_items: 200, multiline: false, fields: [], id_key: null, readonly: false, risk: null, risk_when: [],
    ...overrides,
  };
}

describe("groupFields", () => {
  it("UXP-15 groups and General: a labelled field goes in its group's card, the rest in the first card General", () => {
    const cards = groupFields([f("a"), f("b", { group: "Timing" }), f("c", { group: "Timing" }), f("d"), f("e", { group: "Limits" })]);
    expect(cards.map((card) => card.title)).toEqual(["General", "Timing", "Limits"]);
    expect(cards.map((card) => card.fields.map((field) => field.key))).toEqual([["a", "d"], ["b", "c"], ["e"]]);
  });

  it("UXP-15 General comes first even when a labelled field comes before the first unlabelled one", () => {
    const cards = groupFields([f("b", { group: "Timing" }), f("a")]);
    expect(cards.map((card) => card.title)).toEqual(["General", "Timing"]);
  });

  it("UXP-15 a section with no labels keeps one card, and with only labels has no General card", () => {
    expect(groupFields([f("a"), f("b"), f("c")]).map((card) => [card.title, card.fields.length])).toEqual([["General", 3]]);
    expect(groupFields([f("a", { group: "One" }), f("b", { group: "Two" })]).map((card) => card.title)).toEqual(["One", "Two"]);
    expect(groupFields([])).toEqual([]);
  });

  it("UXP-16 a records or list field is its own card titled by its label, whatever its group", () => {
    const cards = groupFields([
      f("a"),
      f("profiles", { kind: "records", label: "Profiles", group: "Timing" }),
      f("b"),
      f("targets", { kind: "list", label: "Targets" }),
    ]);
    expect(cards.map((card) => [card.title, card.solo, card.fields.map((field) => field.key)])).toEqual([
      ["General", false, ["a", "b"]],
      ["Profiles", true, ["profiles"]],
      ["Targets", true, ["targets"]],
    ]);
  });
});
