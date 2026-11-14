function failedSerialization(): string {
  console.warn("Unable to serialize JSON-LD entry; skipping structured data.");
  return "null";
}

/** Serialize JSON for a script raw-text element, including untrusted strings. */
export function serializeScriptJson(value: unknown): string {
  try {
    const serialized = JSON.stringify(value);
    if (serialized === undefined) {
      return failedSerialization();
    }
    return serialized.replace(
      /[<>&\u2028\u2029]/g,
      (character) => `\\u${character.charCodeAt(0).toString(16).padStart(4, "0")}`,
    );
  } catch {
    // Circular values, BigInt and throwing toJSON methods have no JSON form.
    return failedSerialization();
  }
}
