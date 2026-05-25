import { useState, type SyntheticEvent } from "react";
import { requestMagicLink } from "../lib/api";

interface SignInProps {
  redirectPath?: string;
}

export function SignIn({ redirectPath = "/account" }: SignInProps) {
  const [email, setEmail] = useState("");
  const [isSending, setIsSending] = useState(false);
  const [isSent, setIsSent] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: SyntheticEvent<HTMLFormElement>) {
    event.preventDefault();
    setIsSending(true);
    setError(null);
    try {
      await requestMagicLink({ email, redirect_path: redirectPath });
      setIsSent(true);
    } catch {
      setError("Unable to send a sign-in link right now.");
    } finally {
      setIsSending(false);
    }
  }

  if (isSent) {
    return (
      <div
        role="status"
        className="rounded-lg border border-emerald-500/30 bg-emerald-500/10 p-5 text-emerald-100"
      >
        Check your email for a sign-in link. It can be used once and expires shortly.
      </div>
    );
  }

  return (
    <form className="space-y-5" onSubmit={(event) => void submit(event)}>
      {error && (
        <div
          role="alert"
          className="rounded-lg border border-red-500/30 bg-red-500/10 p-4 text-red-100"
        >
          {error}
        </div>
      )}
      <div>
        <label
          htmlFor="account-email"
          className="mb-2 block text-sm font-medium text-text-secondary"
        >
          Email address
        </label>
        <input
          id="account-email"
          aria-label="Email address"
          className="input w-full"
          type="email"
          autoComplete="email"
          required
          value={email}
          onChange={(event) => setEmail(event.target.value)}
          placeholder="you@example.com"
        />
      </div>
      <button type="submit" className="btn btn-primary w-full" disabled={isSending}>
        {isSending ? "Sending link..." : "Email me a sign-in link"}
      </button>
    </form>
  );
}
