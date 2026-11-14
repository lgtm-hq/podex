import { describe, expect, it } from "vitest";

import { GET } from "./health";

describe("GET /health", () => {
  it("serves uncached liveness without calling a backend", async () => {
    const response = GET();
    expect(response.status).toBe(200);
    expect(response.headers.get("Content-Type")).toBe("application/json");
    expect(response.headers.get("Cache-Control")).toBe("no-store");
    expect(await response.json()).toEqual({ status: "ok" });
  });
});
