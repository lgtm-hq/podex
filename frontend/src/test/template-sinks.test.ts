// This lexical guard requires review for raw-HTML operations in non-test Astro,
// TSX, TS, JS and MJS source, and complete script elements in Astro templates.
// It does not follow data flow or cover href/src values or computed sink names.
import { mkdtempSync, readFileSync, readdirSync, rmSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
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
    source: "set:html={serialized}",
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
      set:html={serialized}
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
      if (
        !/\.(?:astro|tsx|jsx|ts|mts|cts|js|mjs|cjs)$/.test(entry.name) ||
        /\.(?:test|spec)\.[^.]+$/.test(entry.name)
      ) {
        return [];
      }
      const source = readFileSync(path, "utf8");
      const rawHtml =
        /\bset\s*:\s*html\s*=\s*(?:\{[^}]*\}|"[^"]*"|'[^']*')|\b(?:dangerouslySetInnerHTML|innerHTML|outerHTML|insertAdjacentHTML|setHTMLUnsafe|createContextualFragment|[Ss][Rr][Cc][Dd][Oo][Cc])\b|\bdocument\s*\.\s*write(?:ln)?\b/g;
      // Frontmatter may document <script> in comments. Script elements live
      // below it; raw-HTML operations are checked in both sections. Only a
      // block that opens on the first line of the file is frontmatter.
      const template = entry.name.endsWith(".astro") ? source.replace(/^---\r?\n[\s\S]*?\r?\n---/, "") : "";
      const scripts = /<script\b[^>]*(?:\/>|>[\s\S]*?<\/script\b[^>]*>)/gi;
      return [
        ...Array.from(source.matchAll(rawHtml)),
        ...Array.from(template.matchAll(scripts)),
      ].map(([sink]) => ({ file, source: normalize(sink) }));
    });
}

function assertReviewedSinks(directory = sourceRoot, reviewed = reviewedSinks): void {
  expect(collectSinks(directory)).toEqual(reviewed);
}

describe("Astro template sinks", () => {
  it("allows only the reviewed sinks in all source templates", () => {
    assertReviewedSinks();
  });

  it.each([
    ["astro", "<div set:html={catalogText} />"],
    ["astro", "<div dangerouslySetInnerHTML={{ __html: catalogText }} />"],
    ["astro", "---\ndocument.body.innerHTML = catalogText;\n---\n<div />"],
    ["astro", '<script is:inline type="application/ld+json">{JSON.stringify(entry)}</script>'],
    [
      "astro",
      '<script is:inline type="application/ld+json">{JSON.stringify(entry)}</script data-x>',
    ],
    [
      "astro",
      '<script is:inline type="application/ld+json">{JSON.stringify(entry)}</script\t\n bar>',
    ],
    ["tsx", "<div dangerouslySetInnerHTML={{ __html: catalogText }} />"],
    ["ts", "element.innerHTML = catalogText;"],
    ["js", "element.outerHTML = catalogText;"],
    ["mjs", 'element.insertAdjacentHTML("beforeend", catalogText);'],
    ["astro", "---\ndocument.write(catalogText);\n---\n<div />"],
    ["tsx", "<iframe srcdoc={catalogText} />"],
    ["tsx", "<iframe srcDoc={catalogText} />"],
    ["astro", "<iframe SRCDOC={catalogText} />"],
    ["ts", "document.writeln(catalogText);"],
    ["ts", "element.setHTMLUnsafe(catalogText);"],
    ["ts", "range.createContextualFragment(catalogText);"],
    ["jsx", "<div dangerouslySetInnerHTML={{ __html: catalogText }} />"],
    ["astro", "<p>intro</p>\n---\n<script is:inline>{catalogText}</script>\n---\n"],
  ])("rejects an unreviewed temporary source (%#)", (extension, source) => {
    const fixture = mkdtempSync(join(tmpdir(), "podex-unreviewed-"));
    try {
      writeFileSync(join(fixture, `Sink.${extension}`), source);
      expect(() => assertReviewedSinks(fixture, [])).toThrow();
    } finally {
      rmSync(fixture, { recursive: true, force: true });
    }
  });

  it.each(["ts", "tsx", "js", "mjs"])(
    "omits %s test fixture literals from production scanning",
    (extension) => {
      const fixture = mkdtempSync(join(tmpdir(), "podex-test-source-"));
      try {
        for (const suffix of ["test", "spec"]) {
          writeFileSync(
            join(fixture, `Sink.${suffix}.${extension}`),
            'const fixture = "element.innerHTML = value";',
          );
        }
        assertReviewedSinks(fixture, []);
      } finally {
        rmSync(fixture, { recursive: true, force: true });
      }
    },
  );
});
