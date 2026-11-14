/** Serialize JSON for a script raw-text element, including untrusted strings. */
export function serializeScriptJson(value: unknown): string {
  try {
    return (JSON.stringify(value) ?? "null").replace(
      /[<>&\u2028\u2029]/g,
      (character) => `\\u${character.charCodeAt(0).toString(16).padStart(4, "0")}`,
    );
  } catch {
    // Circular values, BigInt and throwing toJSON methods have no JSON form.
    return "null";
  }
}
