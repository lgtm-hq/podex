import { describe, expect, it } from "vitest";

import { SITE_URL, absoluteUrl } from "./site";

describe("SITE_URL", () => {
  it("never has a trailing slash", () => {
    expect(SITE_URL.endsWith("/")).toBe(false);
  });
});

describe("absoluteUrl", () => {
  it("prepends a leading slash when the caller forgot one", () => {
    expect(absoluteUrl("legal/terms")).toBe(`${SITE_URL}/legal/terms`);
  });

  it("keeps a single slash between origin and path", () => {
    expect(absoluteUrl("/legal/privacy")).toBe(`${SITE_URL}/legal/privacy`);
  });

  it("returns the origin plus '/' for the root path", () => {
    expect(absoluteUrl("/")).toBe(`${SITE_URL}/`);
  });
});
