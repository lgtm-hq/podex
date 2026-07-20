import { beforeEach, describe, expect, it, vi } from "vitest";

import { SENTRY_DSN, resolveSentryDsn } from "./config";
import { initSentry, scrubEvent } from "./sentry";

const initMock = vi.hoisted(() => vi.fn());

vi.mock("@sentry/browser", () => ({
  init: initMock,
}));

describe("resolveSentryDsn", () => {
  it.each([
    { label: "undefined", raw: undefined },
    { label: "empty", raw: "" },
    { label: "blank", raw: "   " },
  ])("returns undefined for a $label value", ({ raw }) => {
    expect(resolveSentryDsn(raw)).toBeUndefined();
  });

  it("returns a trimmed DSN when configured", () => {
    expect(resolveSentryDsn(" https://key@sentry.example/1 ")).toBe(
      "https://key@sentry.example/1",
    );
  });

  it("resolves to undefined by default (no PUBLIC_SENTRY_DSN in env)", () => {
    expect(SENTRY_DSN).toBeUndefined();
  });
});

describe("scrubEvent", () => {
  it("redacts email addresses in the message", () => {
    const event = { message: "login failed for alice+test@example.co.uk today" };
    expect(scrubEvent(event).message).toBe(
      "login failed for [redacted-email] today",
    );
  });

  it("redacts email addresses in string extra values only", () => {
    const event = {
      extra: { recipient: "bob@example.com", attempts: 3 },
    };
    expect(scrubEvent(event).extra).toEqual({
      recipient: "[redacted-email]",
      attempts: 3,
    });
  });

  it("drops user.email and scrubs remaining user strings", () => {
    const event = {
      user: { email: "carol@example.com", id: "u-1", username: "carol@example.com" },
    };
    expect(scrubEvent(event).user).toEqual({
      id: "u-1",
      username: "[redacted-email]",
    });
  });

  it("leaves events without message, extra, or user untouched", () => {
    const event = {};
    expect(scrubEvent(event)).toEqual({});
  });
});

describe("initSentry", () => {
  beforeEach(() => {
    initMock.mockClear();
  });

  it("does not initialize the SDK when the DSN is missing", async () => {
    await expect(initSentry(undefined)).resolves.toBe(false);
    expect(initMock).not.toHaveBeenCalled();
  });

  it("initializes the SDK with PII disabled when a DSN is configured", async () => {
    await expect(initSentry("https://key@sentry.example/1")).resolves.toBe(true);
    expect(initMock).toHaveBeenCalledExactlyOnceWith({
      dsn: "https://key@sentry.example/1",
      sendDefaultPii: false,
      beforeSend: expect.any(Function),
    });
  });

  it("wires a beforeSend hook that scrubs emails", async () => {
    await initSentry("https://key@sentry.example/1");
    const options = initMock.mock.calls[0]?.[0] as {
      beforeSend: (event: { message?: string }) => { message?: string };
    };
    expect(options.beforeSend({ message: "hi dana@example.org" })).toEqual({
      message: "hi [redacted-email]",
    });
  });
});
