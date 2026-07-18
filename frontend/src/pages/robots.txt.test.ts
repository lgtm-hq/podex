import { describe, expect, it } from "vitest";

import { buildRobotsTxt } from "./robots.txt";

describe("buildRobotsTxt", () => {
  it("allows all crawlers and disallows /api/", () => {
    const body = buildRobotsTxt("https://example.test");
    expect(body).toContain("User-agent: *");
    expect(body).toContain("Allow: /");
    expect(body).toContain("Disallow: /api/");
  });

  it("advertises the sitemap URL derived from the site origin", () => {
    const body = buildRobotsTxt("https://example.test");
    expect(body).toContain("Sitemap: https://example.test/sitemap.xml");
  });

  it("strips a trailing slash from the origin before joining", () => {
    const body = buildRobotsTxt("https://example.test/");
    expect(body).toContain("Sitemap: https://example.test/sitemap.xml");
    expect(body).not.toContain("example.test//sitemap");
  });
});
