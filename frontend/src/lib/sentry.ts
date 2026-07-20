/**
 * Browser Sentry error tracking, disabled unless PUBLIC_SENTRY_DSN is set.
 *
 * The SDK is lazy-loaded via dynamic import so the bundle carries zero Sentry
 * cost when no DSN is configured. Events pass through {@link scrubEvent} to
 * strip email addresses before leaving the browser.
 */
import { SENTRY_DSN } from "./config";

const EMAIL_RE = /[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}/g;
const REDACTED = "[redacted-email]";

/** Minimal structural view of a Sentry event for PII scrubbing. */
export interface ScrubbableEvent {
  message?: string;
  extra?: Record<string, unknown>;
  user?: {
    email?: string;
    [key: string]: unknown;
  };
}

function scrubText(value: string): string {
  return value.replace(EMAIL_RE, REDACTED);
}

function scrubValues(data: Record<string, unknown>): Record<string, unknown> {
  return Object.fromEntries(
    Object.entries(data).map(([key, value]) => [
      key,
      typeof value === "string" ? scrubText(value) : value,
    ]),
  );
}

/**
 * beforeSend hook that strips PII (emails) from an event: redacts email
 * addresses in the message and extra values, and drops user.email.
 */
export function scrubEvent<T extends ScrubbableEvent>(event: T): T {
  if (typeof event.message === "string") {
    event.message = scrubText(event.message);
  }
  if (event.extra) {
    event.extra = scrubValues(event.extra);
  }
  if (event.user) {
    const { email: _email, ...rest } = event.user;
    event.user = scrubValues(rest);
  }
  return event;
}

/**
 * Initialize Sentry when a DSN is configured.
 *
 * No-ops (never importing the SDK) when the DSN is missing, so the default
 * deployment has no Sentry activity at all.
 *
 * @returns true when the SDK was initialized, false when disabled.
 */
export async function initSentry(
  dsn: string | undefined = SENTRY_DSN,
): Promise<boolean> {
  if (!dsn) {
    return false;
  }
  const Sentry = await import("@sentry/browser");
  Sentry.init({
    dsn,
    sendDefaultPii: false,
    beforeSend: (event) => scrubEvent(event),
  });
  return true;
}
