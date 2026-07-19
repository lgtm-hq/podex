import { type ReactNode, useEffect, useState } from "react";

import { clearOpsKey, getOpsKey, setOpsKey } from "../lib/ops";

const NAV = [
  { href: "/ops", label: "Dashboard" },
  { href: "/ops/podcasts", label: "Podcasts" },
  { href: "/ops/pipelines", label: "Pipelines" },
  { href: "/ops/retention", label: "Retention" },
];

/**
 * Shared operator chrome: prompts for the ops key once per browser session
 * and renders the section navigation. Children only mount once a key is
 * present; a wrong key surfaces as 401s in the child views, where the
 * "change key" affordance lets the operator retry.
 */
export default function OpsShell({
  active,
  children,
}: {
  active: string;
  children: ReactNode;
}) {
  const [key, setKeyState] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [ready, setReady] = useState(false);

  useEffect(() => {
    setKeyState(getOpsKey());
    setReady(true);
  }, []);

  if (!ready) return null;

  if (!key) {
    return (
      <form
        className="max-w-md rounded-lg border border-[color:var(--color-hairline)] bg-[color:var(--color-surface)] p-6"
        onSubmit={(event) => {
          event.preventDefault();
          if (!draft.trim()) return;
          setOpsKey(draft.trim());
          setKeyState(draft.trim());
        }}
      >
        <h2 className="font-display text-2xl">Operator access</h2>
        <p className="mt-2 text-sm text-[color:var(--color-muted)]">
          Enter the ops key for this environment. It is kept only in this
          browser session.
        </p>
        <input
          className="mt-4 w-full rounded border border-[color:var(--color-hairline)] bg-white px-3 py-2 text-sm"
          type="password"
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          placeholder="Ops key"
          aria-label="Ops key"
        />
        <button
          className="mt-4 rounded bg-[color:var(--color-accent)] px-4 py-2 text-sm text-white"
          type="submit"
        >
          Continue
        </button>
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
        <button
          className="ml-auto text-xs text-[color:var(--color-muted)] underline"
          type="button"
          onClick={() => {
            clearOpsKey();
            setKeyState(null);
            setDraft("");
          }}
        >
          Change key
        </button>
      </nav>
      {children}
    </div>
  );
}
