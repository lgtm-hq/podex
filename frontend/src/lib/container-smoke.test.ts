import { describe, expect, expectTypeOf, it } from "vitest";

import catalog from "../test/fixtures/container-smoke-catalog.json";

import type { PodcastPage } from "./types";

describe("frontend container smoke catalog", () => {
  it("satisfies the generated catalog types and renders a real podcast", () => {
    expectTypeOf(catalog).toExtend<PodcastPage>();
    expect(catalog.items).toHaveLength(1);
    expect(catalog.items[0].name).not.toBe("");
    expect(catalog.total).toBe(catalog.items.length);
    expect(catalog.items[0].public_id).toMatch(/^pod_/);
  });
});
