import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    globals: true,
    environment: "happy-dom",
    setupFiles: ["./src/test/setup.ts"],
    include: ["src/**/*.{test,spec}.{ts,tsx}"],
    coverage: {
      provider: "v8",
      include: ["src/lib/**", "src/components/**/*.tsx"],
      exclude: ["src/test/**", "**/*.d.ts"],
      reporter: ["text", "lcovonly", "json-summary"],
      thresholds: { lines: 85, functions: 85, branches: 85, statements: 85 },
    },
  },
});
