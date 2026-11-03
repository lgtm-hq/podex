// @vitest-environment node
import { readFileFromUrl } from "@redocly/openapi-core";
import { describe, expect, it } from "vitest";

describe("OpenAPI resolver with the patched minimatch dependency", () => {
  it.each<[string, string, boolean]>([
    ["https://schemas.example.test/api.json", "schemas.example.test/**", true],
    ["https://schemas.example.test/api.json", "https://schemas.example.test/**", true],
    ["https://schemas.example.test/api.json", "http://schemas.example.test/**", false],
    ["https://other.example.test/api.json", "schemas.example.test/**", false],
  ])("matches %s against %s", async (url, matches, shouldMatch) => {
    let headers: HeadersInit | undefined;
    const result = await readFileFromUrl(url, {
      headers: [{ matches, name: "X-Schema-Source", value: "podex" }],
      customFetch: async (_input: RequestInfo | URL, init?: RequestInit) => {
        headers = init?.headers;
        return new Response("{}", {
          headers: { "content-type": "application/json" },
        });
      },
    });

    expect(headers).toEqual(shouldMatch ? { "X-Schema-Source": "podex" } : {});
    expect(result).toEqual({ body: "{}", mimeType: "application/json" });
  });
});
