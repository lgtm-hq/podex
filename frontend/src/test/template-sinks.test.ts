import { mkdtempSync, readFileSync, readdirSync, rmSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

interface Sink {
  file: string;
  source: string;
}

const sourceRoot = join(process.cwd(), "src");
const normalize = (source: string): string => source.replace(/\s+/g, " ").trim();

// Review the entire script body as well as each raw-HTML directive. Keeping
// these exact sources here makes additions or changes require explicit review.
const reviewedSinks: Sink[] = [
  {
    file: "layouts/Layout.astro",
    source: "set:html={serializeScriptJson(entry)}",
  },
  {
    file: "layouts/Layout.astro",
    // Fixed bundled source; no catalog input is interpolated into this script.
    source: normalize(`<script>
      // Sentry error tracking: no-op (and never loaded) unless
      // PUBLIC_SENTRY_DSN is configured for the deployment.
      import { initSentry } from "../lib/sentry";
      void initSentry();
    </script>`),
  },
  {
    file: "layouts/Layout.astro",
    // Dynamic JSON-LD from page props, serialized at the HTML boundary.
    source: normalize(`<script
      is:inline
      type="application/ld+json"
      set:html={serializeScriptJson(entry)}
    />`),
  },
];

function collectSinks(directory: string, prefix = ""): Sink[] {
  return readdirSync(directory, { withFileTypes: true })
    .sort((left, right) => left.name.localeCompare(right.name))
    .flatMap((entry) => {
      const file = prefix + entry.name;
      const path = join(directory, entry.name);
      if (entry.isDirectory()) {
        return collectSinks(path, `${file}/`);
      }
      if (!entry.name.endsWith(".astro")) {
        return [];
      }
      const source = readFileSync(path, "utf8");
      const rawHtml =
        /\bset\s*:\s*html\s*=\s*(?:\{[^}]*\}|"[^"]*"|'[^']*')|\bdangerouslySetInnerHTML\b|\binnerHTML\b/g;
      // Frontmatter may document <script> in comments. Script elements live
      // below it; raw-HTML operations are checked in both sections.
      const template = source.replace(/^---[\s\S]*?^---/m, "");
      const scripts = /<script\b[^>]*(?:\/>|>[\s\S]*?<\/script\s*>)/gi;
      return [
        ...Array.from(source.matchAll(rawHtml)),
        ...Array.from(template.matchAll(scripts)),
      ].map(([sink]) => ({ file, source: normalize(sink) }));
    });
}

function assertReviewedSinks(): void {
  expect(collectSinks(sourceRoot)).toEqual(reviewedSinks);
}

describe("Astro template sinks", () => {
  it("allows only the reviewed sinks in all source templates", () => {
    assertReviewedSinks();
  });

  it.each([
    "<div set:html={catalogText} />",
    "<div dangerouslySetInnerHTML={{ __html: catalogText }} />",
    "---\ndocument.body.innerHTML = catalogText;\n---\n<div />",
    '<script is:inline type="application/ld+json">{JSON.stringify(entry)}</script>',
  ])("rejects an unreviewed temporary template (%#)", (source) => {
    const fixture = mkdtempSync(join(sourceRoot, "test/unreviewed-"));
    try {
      writeFileSync(join(fixture, "Sink.astro"), source);
      expect(() => assertReviewedSinks()).toThrow();
    } finally {
      rmSync(fixture, { recursive: true, force: true });
    }
  });
});
