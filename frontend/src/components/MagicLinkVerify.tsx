import { useEffect, useState } from "react";

import { AccountApiError, verifyMagicLink } from "../lib/account";

/**
 * Redeems the one-time token from the emailed sign-in link and forwards the
 * signed-in user to their requested destination.
 */
export default function MagicLinkVerify() {
  const [state, setState] = useState<"working" | "failed">("working");

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const fragment = new URLSearchParams(
      window.location.hash.replace(/^#/, ""),
    );
    const token = fragment.get("token");
    if (!token) {
      setState("failed");
      return;
    }
    verifyMagicLink(token)
      .then(() => {
        const redirect = params.get("redirect_path");
        window.location.assign(
          redirect && redirect.startsWith("/") ? redirect : "/account",
        );
      })
      .catch((cause: unknown) => {
        void (cause instanceof AccountApiError);
        setState("failed");
      });
  }, []);

  if (state === "failed") {
    return (
      <div className="max-w-md rounded-lg border border-[color:var(--color-hairline)] bg-[color:var(--color-surface)] p-6">
        <h2 className="font-display text-2xl">Sign-in link invalid</h2>
        <p className="mt-2 text-sm text-[color:var(--color-muted)]">
          This link has expired or was already used. Request a new one from
          the <a className="underline" href="/account">account page</a>.
        </p>
      </div>
    );
  }

  return (
    <p className="text-sm text-[color:var(--color-muted)]">Signing you in…</p>
  );
}
