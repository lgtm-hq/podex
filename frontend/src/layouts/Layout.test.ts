import { execFileSync } from "node:child_process";
import { mkdtempSync, mkdirSync, readFileSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { Window } from "happy-dom";
import { describe, expect, it } from "vitest";

const projectRoot = process.cwd();
const layoutPath = join(projectRoot, "src/layouts/Layout.astro");

describe("Layout structured data", () => {
  it("keeps hostile catalog text inside the intended JSON-LD scripts", () => {
    const fixture = mkdtempSync(join(tmpdir(), "podex-json-ld-"));
    const name = "</script><script>alert(1)</script>";
    const entry = {
      "@context": "https://schema.org",
      "@type": "PodcastSeries",
      name,
      description: name,
    };
    try {
      const srcDir = join(fixture, "src");
      const outDir = join(fixture, "dist");
      mkdirSync(join(srcDir, "pages"), { recursive: true });
      // Prerendered modules resolve external packages from the temporary output.
      symlinkSync(join(projectRoot, "node_modules"), join(fixture, "node_modules"), "dir");
      for (const [page, jsonLd] of [
        ["index", undefined],
        ["hostile", [entry, { "@type": "BreadcrumbList", name }]],
      ] as const) {
        writeFileSync(
          join(srcDir, "pages", `${page}.astro`),
          `---\nimport Layout from ${JSON.stringify(layoutPath)};\nconst jsonLd = ${JSON.stringify(jsonLd) ?? "undefined"};\n---\n<Layout jsonLd={jsonLd} />`,
        );
      }
      writeFileSync(
        join(srcDir, "pages", "invalid.astro"),
        `---\nimport Layout from ${JSON.stringify(layoutPath)};\nconst circular = {}; circular.self = circular;\nconst jsonLd = [undefined, () => {}, Symbol("private"), 1n, circular, {toJSON() { throw new Error("private"); }}, null, ${JSON.stringify(entry)}];\n---\n<Layout jsonLd={jsonLd} />`,
      );
      for (const [page, endTag] of [
        ["end-tag-attributes", "</script data-x>"],
        ["end-tag-whitespace", "</script\t\n bar>"],
      ]) {
        writeFileSync(
          join(srcDir, "pages", `${page}.astro`),
          `<script is:inline type="application/ld+json">{"name":"probe"}${endTag}`,
        );
      }
      const config = { root: projectRoot, srcDir, outDir, output: "static", logLevel: "silent" };
      execFileSync(
        process.execPath,
        [
          "--input-type=module",
          "--eval",
          `import { build } from "astro"; await build(${JSON.stringify(config)});`,
        ],
        { cwd: projectRoot, stdio: "pipe" },
      );
      const browser = new Window({
        settings: {
          disableCSSFileLoading: true,
          disableJavaScriptFileLoading: true,
          disableJavaScriptEvaluation: true,
          handleDisabledFileLoadingAsSuccess: true,
        },
      });
      const parsePage = (page: string) =>
        new browser.DOMParser().parseFromString(
          readFileSync(join(outDir, "client", page, "index.html"), "utf8"),
          "text/html",
        );
      const baseline = parsePage("");
      for (const page of ["end-tag-attributes", "end-tag-whitespace"]) {
        const probe = parsePage(page).querySelectorAll("script");
        expect(probe).toHaveLength(1);
        expect(JSON.parse(probe[0].textContent ?? "")).toEqual({ name: "probe" });
      }
      const rendered = parsePage("hostile");
      expect(rendered.querySelectorAll("script")).toHaveLength(
        baseline.querySelectorAll("script").length + 2,
      );
      const scripts = rendered.querySelectorAll('script[type="application/ld+json"]');
      expect(scripts).toHaveLength(2);
      expect(Array.from(scripts, (script) => JSON.parse(script.textContent ?? ""))).toEqual([
        entry,
        { "@type": "BreadcrumbList", name },
      ]);
      const invalidScripts = parsePage("invalid").querySelectorAll(
        'script[type="application/ld+json"]',
      );
      expect(invalidScripts).toHaveLength(1);
      expect(JSON.parse(invalidScripts[0].textContent ?? "")).toEqual(entry);
    } finally {
      rmSync(fixture, { recursive: true, force: true });
    }
  }, 30000);
});
