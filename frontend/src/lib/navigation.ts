import { SITE_URL } from "./site";

/** Normalize an untrusted navigation target to a path on this site. */
export function sitePath(
  target: unknown,
  origin: string = typeof window === "undefined"
    ? SITE_URL
    : window.location.origin,
): string {
  const fallback = "/account";
  if (
    typeof target !== "string" ||
    !target.startsWith("/") ||
    target.startsWith("//") ||
    target.startsWith("/\\") ||
    Array.from(target).some((character) => {
      const code = character.charCodeAt(0);
      return code <= 31 || (code >= 127 && code <= 159);
    })
  ) {
    return fallback;
  }

  try {
    // Compare canonical origins, so a base written with a trailing slash, a
    // default port, upper case or a path still identifies this site.
    const base = new URL(origin).origin;
    const resolved = new URL(target, base);
    if (resolved.origin !== base) return fallback;
    // Dot-segment normalization can expose a second leading slash. The path
    // returned here must also be safe when parsed again by a navigation sink.
    if (
      resolved.pathname.startsWith("//") ||
      resolved.pathname.startsWith("/\\")
    ) {
      return fallback;
    }
    return `${resolved.pathname}${resolved.search}${resolved.hash}`;
  } catch {
    return fallback;
  }
}
