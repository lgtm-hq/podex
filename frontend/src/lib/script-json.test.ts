import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { serializeScriptJson } from "./script-json";

describe("serializeScriptJson", () => {
  beforeEach(() => {
    vi.spyOn(console, "warn").mockImplementation(() => undefined);
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("escapes HTML delimiters and separators without changing JSON values", () => {
    const value = {
      name: "</script><script>alert(1)</script>",
      description: "<!-- ]]> & \u2028\u2029 café 🎧",
      nested: [{ text: "</ScRiPt>" }],
    };
    const json = serializeScriptJson(value);
    expect(json).not.toMatch(/[<>&\u2028\u2029]/u);
    expect(json).toContain("\\u003c/script\\u003e");
    expect(json).toContain("\\u003c!--");
    expect(json).toContain("\\u0026");
    expect(json).toContain("\\u2028\\u2029");
    expect(JSON.parse(json)).toEqual(value);
  });

  it.each([null, true, false, 0, 42, "text", [], {}, [undefined], { missing: undefined }])(
    "preserves JSON.stringify semantics for %j",
    (value) => {
      expect(JSON.parse(serializeScriptJson(value))).toEqual(JSON.parse(JSON.stringify(value)));
      expect(console.warn).not.toHaveBeenCalled();
    },
  );

  const circular: { self?: unknown } = {};
  circular.self = circular;
  it.each([
    undefined,
    () => "unused",
    Symbol("unused"),
    1n,
    { nested: 1n },
    circular,
    { toJSON: () => undefined },
    {
      toJSON: () => {
        throw new Error("Cannot serialize");
      },
    },
  ])("returns valid null JSON for an unserializable value (%#)", (value) => {
    expect(serializeScriptJson(value)).toBe("null");
    expect(console.warn).toHaveBeenCalledExactlyOnceWith(
      "Unable to serialize JSON-LD entry; skipping structured data.",
    );
  });
});
