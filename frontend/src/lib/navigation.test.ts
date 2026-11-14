import { describe, expect, it } from "vitest";

import { sitePath } from "./navigation";

const origin = "https://podex.example";

describe("sitePath", () => {
  it.each([
    null,
    undefined,
    42,
    {},
    [],
    "",
    "account",
    "//other.example",
    "/\\other.example",
    "https://other.example",
    "javascript:alert(1)",
    " /account",
    "\t/account",
    "/\t/other.example",
    "/\r/other.example",
    "/\n/other.example",
    "/a/..//other.example",
    "/%2e%2e//other.example",
  ])("falls back for %j", (target) => {
    expect(sitePath(target, origin)).toBe("/account");
  });

  it.each(
    Array.from({ length: 160 }, (_, code) => code).filter(
      (code) => code <= 31 || code >= 127,
    ),
  )("rejects control character %i anywhere in the target", (code) => {
    expect(sitePath(`/account?next=${String.fromCharCode(code)}`, origin)).toBe(
      "/account",
    );
  });

  it.each([
    ["/account/settings?tab=alerts#top", "/account/settings?tab=alerts#top"],
    ["/", "/"],
    ["/a/../account?tab=alerts#top", "/account?tab=alerts#top"],
    ["/account\\settings", "/account/settings"],
    ["/account?q=hello world", "/account?q=hello%20world"],
    ["/%2F%2Fother.example", "/%2F%2Fother.example"],
    ["/%5Cother.example", "/%5Cother.example"],
    ["/%09/other.example", "/%09/other.example"],
  ])("normalizes %j to %j", (target, expected) => {
    const result = sitePath(target, origin);
    expect(result).toBe(expected);
    expect(new URL(result, origin).origin).toBe(origin);
  });

  it.each([
    "https://podex.example/",
    "https://podex.example:443",
    "https://PODEX.example",
    "https://podex.example/base",
  ])("accepts a site path when the base is written as %s", (base) => {
    expect(sitePath("/account/settings?tab=alerts#top", base)).toBe(
      "/account/settings?tab=alerts#top",
    );
    expect(sitePath("//other.example", base)).toBe("/account");
  });

  it("uses the current browser origin by default", () => {
    expect(sitePath("/account/saved")).toBe("/account/saved");
  });

  it("falls back when the base cannot be parsed", () => {
    expect(sitePath("/account/saved", "invalid")).toBe("/account");
  });
});
