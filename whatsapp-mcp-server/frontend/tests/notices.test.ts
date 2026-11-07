import { beforeEach, describe, expect, it } from "vitest";
import { REPEAT_WINDOW_MS, noticeText, raiseNotice, resetNoticeMemory } from "../src/lib/notices";
import { useUiStore } from "../src/stores/ui";

const texts = () => useUiStore.getState().notices.map((notice) => notice.text);

beforeEach(() => {
  resetNoticeMemory();
  useUiStore.setState(useUiStore.getInitialState(), true);
});

describe("notice texts", () => {
  it.each([
    ["saved", "Changes saved."],
    ["refused", "Request refused. Reload the page."],
    ["connectionLost", "The interface lost its connection."],
    ["sessionEnded", "Your session ended because of inactivity."],
  ] as const)("%s reads %s", (kind, text) => {
    expect(noticeText(kind)).toBe(text);
  });

  it("fills the minutes of the session warning", () => {
    expect(noticeText("sessionWarning", { minutes: 2 })).toBe("Your session ends in 2 min because of inactivity.");
  });
});

describe("raising a notice", () => {
  it("queues the notice with its catalog text", () => {
    const shown = raiseNotice("saved", undefined, 1_000);
    expect(shown?.text).toBe("Changes saved.");
    expect(texts()).toEqual(["Changes saved."]);
  });

  it("skips the same notice inside 60 seconds", () => {
    raiseNotice("refused", undefined, 10_000);
    expect(raiseNotice("refused", undefined, 10_000 + REPEAT_WINDOW_MS - 1)).toBeNull();
    expect(texts()).toHaveLength(1);
  });

  it("shows it again once 60 seconds have passed", () => {
    raiseNotice("refused", undefined, 10_000);
    expect(raiseNotice("refused", undefined, 10_000 + REPEAT_WINDOW_MS)).not.toBeNull();
    expect(texts()).toHaveLength(2);
  });

  it("does not let one kind suppress another", () => {
    raiseNotice("refused", undefined, 5_000);
    expect(raiseNotice("connectionLost", undefined, 5_001)).not.toBeNull();
    expect(texts()).toHaveLength(2);
  });

  it("does not count a skipped repeat as a new showing", () => {
    raiseNotice("refused", undefined, 0);
    raiseNotice("refused", undefined, 50_000);
    expect(raiseNotice("refused", undefined, 60_000)).not.toBeNull();
  });
});

describe("what a notice may carry", () => {
  it("takes numbers only, so no form value or secret can enter the text", () => {
    const hostile = { minutes: 2, secret: "hunter2" } as unknown as Record<string, number>;
    const shown = raiseNotice("sessionWarning", hostile, 1);
    expect(shown?.text).toBe("Your session ends in 2 min because of inactivity.");
    expect(JSON.stringify(useUiStore.getState().notices)).not.toContain("hunter2");

    const textual = { minutes: "hunter2" } as unknown as Record<string, number>;
    expect(noticeText("sessionWarning", textual)).not.toContain("hunter2");
  });
});
