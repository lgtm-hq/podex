/** Base URL for the Podex v2 API. */
export const API_BASE_URL: string =
  import.meta.env.PUBLIC_API_URL ?? "http://localhost:8000/api/v2";

/**
 * Resolve the Sentry DSN from a raw env value.
 *
 * Returns undefined for missing/blank values so Sentry stays fully disabled
 * unless a DSN is deployed via PUBLIC_SENTRY_DSN.
 */
export function resolveSentryDsn(
  raw: string | undefined = import.meta.env.PUBLIC_SENTRY_DSN,
): string | undefined {
  const dsn = raw?.trim();
  return dsn ? dsn : undefined;
}

/** Sentry DSN, or undefined when error tracking is disabled (the default). */
export const SENTRY_DSN: string | undefined = resolveSentryDsn();
