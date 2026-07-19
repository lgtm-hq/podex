import { useEffect, useState } from "react";

import { type DigestList, getDigests } from "../lib/account";
import AccountShell from "./AccountShell";

/** Account overview: identity summary and recent digest activity. */
export default function AccountOverview() {
  return (
    <AccountShell active="/account">
      {(user) => <OverviewBody email={user.email} createdAt={user.created_at} />}
    </AccountShell>
  );
}

function OverviewBody({
  email,
  createdAt,
}: {
  email: string;
  createdAt: string;
}) {
  const [digests, setDigests] = useState<DigestList | null>(null);

  useEffect(() => {
    getDigests()
      .then(setDigests)
      .catch(() => setDigests({ items: [], total: 0 }));
  }, []);

  return (
    <div>
      <div className="max-w-xl rounded-lg border border-[color:var(--color-hairline)] bg-[color:var(--color-surface)] p-5">
        <p className="text-xs uppercase tracking-[0.2em] text-[color:var(--color-muted)]">
          Signed in as
        </p>
        <p className="font-display mt-2 text-2xl">{email}</p>
        <p className="mt-1 text-sm text-[color:var(--color-muted)]">
          Member since {new Date(createdAt).toLocaleDateString()}
        </p>
      </div>

      <h2 className="font-display mt-10 text-2xl">Recent digests</h2>
      {digests === null ? (
        <p className="mt-3 text-sm text-[color:var(--color-muted)]">Loading…</p>
      ) : digests.items.length === 0 ? (
        <p className="mt-3 text-sm text-[color:var(--color-muted)]">
          No digests delivered yet. Follow podcasts and save media, then set
          up alerts to receive activity digests.
        </p>
      ) : (
        <ul className="mt-3 space-y-3">
          {digests.items.map((digest) => (
            <li
              key={digest.id}
              className="rounded-lg border border-[color:var(--color-hairline)] p-4"
            >
              <p className="font-medium">{digest.subject}</p>
              <p className="mt-1 text-xs text-[color:var(--color-muted)]">
                {digest.event_count} update{digest.event_count === 1 ? "" : "s"}
                {" · "}
                {new Date(digest.created_at).toLocaleString()}
              </p>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
