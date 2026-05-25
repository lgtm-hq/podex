import { useEffect, useState } from "react";
import { verifyMagicLink } from "../lib/api";

interface MagicLinkVerificationProps {
  token: string;
  redirectPath?: string;
}

export function MagicLinkVerification({
  token,
  redirectPath = "/account",
}: MagicLinkVerificationProps) {
  const [state, setState] = useState<"checking" | "verified" | "failed">("checking");

  useEffect(() => {
    verifyMagicLink(token)
      .then(() => setState("verified"))
      .catch(() => setState("failed"));
  }, [token]);

  if (state === "checking") {
    return (
      <p role="status" className="text-text-secondary">
        Verifying your sign-in link...
      </p>
    );
  }
  if (state === "failed") {
    return (
      <div role="alert" className="space-y-4">
        <p className="text-red-200">This sign-in link is invalid or has expired.</p>
        <a className="btn btn-primary" href="/sign-in">
          Request a new link
        </a>
      </div>
    );
  }
  return (
    <div role="status" className="space-y-4">
      <p className="text-text-secondary">
        You are signed in. Your session is active on this browser.
      </p>
      <a className="btn btn-primary" href={redirectPath}>
        Continue to Podex
      </a>
    </div>
  );
}
