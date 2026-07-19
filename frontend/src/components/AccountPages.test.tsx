import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import AccountAlerts from "./AccountAlerts";
import AccountFollows from "./AccountFollows";
import AccountOverview from "./AccountOverview";
import AccountSaved from "./AccountSaved";
import AccountSettings from "./AccountSettings";
import AccountShell from "./AccountShell";
import MagicLinkVerify from "./MagicLinkVerify";

const USER = {
  id: 1,
  email: "reader@example.com",
  created_at: "2026-07-01T00:00:00Z",
  last_signed_in_at: null,
};

const SAVED = {
  items: [
    {
      media: {
        id: 3,
        type: "book",
        title: "Dune",
        author: "Herbert",
        year: 1965,
        description: null,
        cover_url: null,
        created_at: "2026-07-01T00:00:00Z",
        public_id: "med_ae",
      },
      saved_at: "2026-07-10T00:00:00Z",
    },
  ],
  total: 1,
};

const FOLLOWS = {
  items: [
    {
      podcast: {
        id: 2,
        name: "The Example Show",
        slug: "example-show",
        description: null,
        created_at: "2026-07-01T00:00:00Z",
        public_id: "pod_ae",
      },
      followed_at: "2026-07-10T00:00:00Z",
    },
  ],
  total: 1,
};

const RULES = {
  items: [
    {
      id: 9,
      target_type: "podcast",
      target_id: 2,
      event_type: "new_episode",
      enabled: true,
      baseline_count: 1,
      last_evaluated_at: null,
      created_at: "2026-07-10T00:00:00Z",
    },
  ],
  total: 1,
};

const PREFERENCE = {
  digest_enabled: true,
  digest_frequency: "daily",
  updated_at: "2026-07-10T00:00:00Z",
};

function mockRoutes(
  routes: Record<string, unknown>,
  overrides: Record<string, (init?: RequestInit) => unknown> = {},
) {
  const fetchMock = vi.fn((url: string, init?: RequestInit) => {
    const matched = Object.entries(overrides).find(([fragment]) =>
      String(url).includes(fragment),
    );
    if (matched) {
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve(matched[1](init)),
      } as unknown as Response);
    }
    const found = Object.entries(routes).find(([fragment]) =>
      String(url).endsWith(fragment),
    );
    if (!found) {
      return Promise.resolve({
        ok: false,
        status: 404,
        json: () => Promise.resolve({}),
      } as unknown as Response);
    }
    return Promise.resolve({
      ok: true,
      status: 200,
      json: () => Promise.resolve(found[1]),
    } as unknown as Response);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function mockAnonymous() {
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string, init?: RequestInit) => {
      if (String(url).endsWith("/me")) {
        return Promise.resolve({
          ok: false,
          status: 401,
          json: () => Promise.resolve({}),
        } as unknown as Response);
      }
      if (String(url).includes("/auth/magic-link/request")) {
        return Promise.resolve({
          ok: true,
          status: 202,
          json: () => Promise.resolve({ accepted: true }),
        } as unknown as Response);
      }
      void init;
      return Promise.resolve({
        ok: false,
        status: 404,
        json: () => Promise.resolve({}),
      } as unknown as Response);
    }),
  );
}

describe("account pages", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("AccountShell offers passwordless sign-in when anonymous", async () => {
    mockAnonymous();

    render(<AccountShell active="/account">{() => <p>body</p>}</AccountShell>);

    const input = await screen.findByLabelText("Email address");
    fireEvent.change(input, { target: { value: "reader@example.com" } });
    fireEvent.click(screen.getByText("Email me a link"));

    expect(await screen.findByText(/Check your email/)).toBeDefined();
  });

  it("AccountShell reports unavailable sign-in delivery", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((url: string) =>
        Promise.resolve({
          ok: false,
          status: String(url).endsWith("/me") ? 401 : 503,
          json: () => Promise.resolve({}),
        } as unknown as Response),
      ),
    );

    render(<AccountShell active="/account">{() => <p>body</p>}</AccountShell>);
    const input = await screen.findByLabelText("Email address");
    fireEvent.change(input, { target: { value: "reader@example.com" } });
    fireEvent.click(screen.getByText("Email me a link"));

    expect(
      await screen.findByText("Email sign-in is not available right now."),
    ).toBeDefined();
  });

  it("AccountShell shows a generic account error", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("net")));

    render(<AccountShell active="/account">{() => <p>body</p>}</AccountShell>);

    expect(
      await screen.findByText("Unable to load your account right now."),
    ).toBeDefined();
  });

  it("AccountOverview shows identity and digests", async () => {
    mockRoutes({
      "/me/digests": {
        items: [
          {
            id: 5,
            channel: "email",
            subject: "Podex digest: 2 new updates",
            body_text: "…",
            event_count: 2,
            created_at: "2026-07-18T00:00:00Z",
            delivered_at: "2026-07-18T00:00:00Z",
          },
        ],
        total: 1,
      },
      "/me": USER,
    });

    render(<AccountOverview />);

    expect((await screen.findAllByText("reader@example.com")).length).toBe(2);
    expect(
      await screen.findByText("Podex digest: 2 new updates"),
    ).toBeDefined();
  });

  it("AccountSaved lists and removes saved media", async () => {
    const fetchMock = mockRoutes(
      { "/me/saves": SAVED, "/me": USER },
      {},
    );

    render(<AccountSaved />);
    expect(await screen.findByText("Dune")).toBeDefined();

    fireEvent.click(screen.getByText("Remove"));
    await waitFor(() => {
      expect(
        fetchMock.mock.calls.some(([, init]) => init?.method === "DELETE"),
      ).toBe(true);
    });
  });

  it("AccountFollows lists and unfollows podcasts", async () => {
    const fetchMock = mockRoutes({ "/me/follows": FOLLOWS, "/me": USER });

    render(<AccountFollows />);
    expect(await screen.findByText("The Example Show")).toBeDefined();

    fireEvent.click(screen.getByText("Unfollow"));
    await waitFor(() => {
      expect(
        fetchMock.mock.calls.some(([, init]) => init?.method === "DELETE"),
      ).toBe(true);
    });
  });

  it("AccountAlerts pauses and deletes rules", async () => {
    const fetchMock = mockRoutes(
      { "/me/alerts": RULES, "/me": USER },
      { "/me/alerts/9": () => ({ ...RULES.items[0], enabled: false }) },
    );

    render(<AccountAlerts />);
    expect(
      await screen.findByText(/New episodes from followed podcast/),
    ).toBeDefined();

    fireEvent.click(screen.getByText("Pause"));
    await waitFor(() => {
      expect(
        fetchMock.mock.calls.some(([, init]) => init?.method === "PATCH"),
      ).toBe(true);
    });

    fireEvent.click(screen.getByText("Delete"));
    await waitFor(() => {
      expect(
        fetchMock.mock.calls.some(([, init]) => init?.method === "DELETE"),
      ).toBe(true);
    });
  });

  it("AccountSaved shows empty and error states", async () => {
    mockRoutes({ "/me/saves": { items: [], total: 0 }, "/me": USER });
    const { unmount } = render(<AccountSaved />);
    expect(await screen.findByText(/Nothing saved yet/)).toBeDefined();
    unmount();

    mockRoutes({ "/me": USER });
    render(<AccountSaved />);
    expect(
      await screen.findByText("Unable to load saved media."),
    ).toBeDefined();
  });

  it("AccountFollows shows empty and error states", async () => {
    mockRoutes({ "/me/follows": { items: [], total: 0 }, "/me": USER });
    const { unmount } = render(<AccountFollows />);
    expect(
      await screen.findByText("You are not following any podcasts yet."),
    ).toBeDefined();
    unmount();

    mockRoutes({ "/me": USER });
    render(<AccountFollows />);
    expect(
      await screen.findByText("Unable to load followed podcasts."),
    ).toBeDefined();
  });

  it("AccountAlerts reports load failures", async () => {
    mockRoutes({ "/me": USER });

    render(<AccountAlerts />);

    expect(
      await screen.findByText("Unable to load alert rules."),
    ).toBeDefined();
  });

  it("AccountSettings reports load failures", async () => {
    mockRoutes({ "/me": USER });

    render(<AccountSettings />);

    expect(
      await screen.findByText("Unable to load preferences."),
    ).toBeDefined();
  });

  it("AccountAlerts shows the empty state", async () => {
    mockRoutes({ "/me/alerts": { items: [], total: 0 }, "/me": USER });

    render(<AccountAlerts />);

    expect(await screen.findByText(/No alert rules yet/)).toBeDefined();
  });

  it("AccountSettings persists preference changes", async () => {
    const fetchMock = mockRoutes(
      { "/me/preferences": PREFERENCE, "/me": USER },
      {},
    );

    render(<AccountSettings />);
    const checkbox = await screen.findByRole("checkbox");
    fireEvent.click(checkbox);

    await waitFor(() => {
      expect(
        fetchMock.mock.calls.some(([, init]) => init?.method === "PATCH"),
      ).toBe(true);
    });
    expect(await screen.findByText("Saved.")).toBeDefined();
  });

  it("AccountSettings exports data and deletes with confirmation", async () => {
    const fetchMock = mockRoutes(
      { "/me/preferences": PREFERENCE, "/me": USER, "/me/export": { a: 1 } },
      {},
    );
    vi.stubGlobal("URL", {
      createObjectURL: vi.fn(() => "blob:x"),
      revokeObjectURL: vi.fn(),
    });
    const assign = vi.fn();
    vi.stubGlobal("location", { ...window.location, assign });

    render(<AccountSettings />);
    fireEvent.click(await screen.findByText("Export my data"));
    await waitFor(() => {
      expect(
        fetchMock.mock.calls.some(([url]) =>
          String(url).endsWith("/me/export"),
        ),
      ).toBe(true);
    });

    fireEvent.click(screen.getByText("Delete account"));
    fireEvent.click(await screen.findByText("Cancel"));
    fireEvent.click(screen.getByText("Delete account"));
    fireEvent.click(await screen.findByText("Yes, delete everything"));
    await waitFor(() => {
      expect(
        fetchMock.mock.calls.some(([, init]) => init?.method === "DELETE"),
      ).toBe(true);
    });
  });

  it("MagicLinkVerify redeems the token and redirects", async () => {
    mockRoutes({
      "/auth/magic-link/verify": {
        user: USER,
        expires_at: "2026-08-18T00:00:00Z",
      },
    });
    const assign = vi.fn();
    vi.stubGlobal("location", {
      ...window.location,
      search: "?redirect_path=/account/saved",
      hash: "#token=abc",
      assign,
    });

    render(<MagicLinkVerify />);

    await waitFor(() => {
      expect(assign).toHaveBeenCalledWith("/account/saved");
    });
  });

  it("MagicLinkVerify explains invalid links", async () => {
    vi.stubGlobal("location", {
      ...window.location,
      search: "",
      hash: "",
      assign: vi.fn(),
    });

    render(<MagicLinkVerify />);

    expect(await screen.findByText("Sign-in link invalid")).toBeDefined();
  });
});
