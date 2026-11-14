// @vitest-environment node
// GHSA-r3h5-4585-7x3w: the @astrojs/node adapter serves /_image through
// astro's `astro/assets/endpoint/node`, which confines local file reads to the
// client output directory with `isParentDirectory`. Before astro 7.4.7 that
// check ignored letter case, so on Linux a sibling such as `/app/DIST/client`
// passed as being inside `/app/dist/client`. Exercise the copy astro itself
// resolves, because the adapter may hoist a different internal-helpers release.
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { pathToFileURL } from "node:url";
import { describe, expect, it } from "vitest";

type PathHelpers = {
  isParentDirectory: (parentPath: string, childPath: string) => boolean;
};

const requireFromHere = createRequire(import.meta.url);
const astroManifestPath = requireFromHere.resolve("astro/package.json");
const requireFromAstro = createRequire(astroManifestPath);
const { isParentDirectory } = (await import(
  pathToFileURL(requireFromAstro.resolve("@astrojs/internal-helpers/path")).href
)) as PathHelpers;

const OUTPUT_DIR = "/app/dist/client/";

describe("astro /_image endpoint output-directory check", () => {
  it("guards local reads with the internal-helpers check", () => {
    const endpoint = readFileSync(requireFromAstro.resolve("astro/assets/endpoint/node"), "utf8");

    expect(endpoint).toContain('from "@astrojs/internal-helpers/path"');
    expect(endpoint).toContain("isParentDirectory(");
  });

  it("test_image_endpoint_rejects_case_variant_sibling", () => {
    expect(isParentDirectory(OUTPUT_DIR, "/app/DIST/client/sibling.png")).toBe(false);
    expect(isParentDirectory(OUTPUT_DIR, "/app/Dist/client/sibling.png")).toBe(false);
    // Positive control: an ordinary image inside the output directory.
    expect(isParentDirectory(OUTPUT_DIR, "/app/dist/client/control.png")).toBe(true);
  });
});
