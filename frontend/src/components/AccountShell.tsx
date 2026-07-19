import { type ReactNode, useEffect, useState } from "react";

import {
  AccountApiError,
  type AccountUser,
  getMe,
  logout,
  requestMagicLink,
} from "../lib/account";

const NAV = [
  { href: "/account", label: "Overview" },
  { href: "/account/saved", label: "Saved" },
  { href: "/account/follows", label: "Follows" },
  { href: "/account/alerts", label: "Alerts" },
  { href: "/account/settings", label: "Settings" },
];

/**
 * Shared account chrome: resolves the signed-in user from the session
 * cookie, offers passwordless sign-in when anonymous, and renders section
 * navigation once authenticated.
 */
export default function AccountShell({
  active,
  children,
}: {
  active: string;
  children: (user: AccountUser) => ReactNode;
}) {
  const [user, setUser] = useState<AccountUser | null>(null);
  const [state, setState] = useState<"loading" | "anonymous" | "ready" | "error">(
    "loading",
  );
  const [email, setEmail] = useState("");
  const [requested, setRequested] = useState(false);
  const [signInError, setSignInError] = useState<string | null>(null);

  useEffect(() => {
    getMe()
      .then((me) => {
        setUser(me);
        setState("ready");
      })
      .catch((cause: unknown) => {
        setState(
          cause instanceof AccountApiError && cause.status === 401
            ? "anonymous"
            : "error",
        );
      });
  }, []);

  if (state === "loading") {
    return <p className="text-sm text-[color:var(--color-muted)]">Loading…</p>;
  }

  if (state === "error") {
    return (
      <p className="text-sm text-red-700">
        Unable to load your account right now.
      </p>
    );
  }

  if (state === "anonymous" || user === null) {
    return (
      <form
        className="max-w-md rounded-lg border border-[color:var(--color-hairline)] bg-[color:var(--color-surface)] p-6"
        onSubmit={(event) => {
          event.preventDefault();
          setSignInError(null);
          requestMagicLink(email, active)
            .then(() => setRequested(true))
            .catch((cause: unknown) => {
              setSignInError(
                cause instanceof AccountApiError && cause.status === 503
                  ? "Email sign-in is not available right now."
                  : "Unable to send the sign-in link.",
              );
            });
        }}
      >
        <h2 className="font-display text-2xl">Sign in</h2>
        {requested ? (
          <p className="mt-3 text-sm text-[color:var(--color-muted)]">
            Check your email — if the address is valid, a single-use sign-in
            link is on its way.
          </p>
        ) : (
          <>
            <p className="mt-2 text-sm text-[color:var(--color-muted)]">
              We will email you a single-use sign-in link. No password needed.
            </p>
            <input
              className="mt-4 w-full rounded border border-[color:var(--color-hairline)] bg-white px-3 py-2 text-sm"
              type="email"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              placeholder="you@example.com"
              aria-label="Email address"
              required
            />
            <button
              className="mt-4 rounded bg-[color:var(--color-accent)] px-4 py-2 text-sm text-white"
              type="submit"
            >
              Email me a link
            </button>
            {signInError ? (
              <p className="mt-3 text-sm text-red-700">{signInError}</p>
            ) : null}
          </>
        )}
      </form>
    );
  }

  return (
    <div>
      <nav className="mb-8 flex flex-wrap items-center gap-4 border-b border-[color:var(--color-hairline)] pb-4 text-sm">
        {NAV.map((item) => (
          <a
            key={item.href}
            href={item.href}
            className={
              item.href === active
                ? "font-medium text-[color:var(--color-accent)]"
                : "text-[color:var(--color-muted)] hover:text-[color:var(--color-ink)]"
            }
          >
            {item.label}
          </a>
        ))}
        <span className="ml-auto text-xs text-[color:var(--color-muted)]">
          {user.email}
        </span>
        <button
          className="text-xs underline"
          type="button"
          onClick={() => {
            logout().finally(() => {
              window.location.assign("/account");
            });
          }}
        >
          Sign out
        </button>
      </nav>
      {children(user)}
    </div>
  );
}
