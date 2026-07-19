import { API_BASE_URL } from "./config";
import type { components } from "./types.gen";

export type AccountUser = components["schemas"]["AccountUserRead"];
export type SavedMediaList = components["schemas"]["SavedMediaListRead"];
export type FollowedPodcastList =
  components["schemas"]["FollowedPodcastListRead"];
export type AlertRule = components["schemas"]["AlertRuleRead"];
export type AlertRuleList = components["schemas"]["AlertRuleListRead"];
export type DigestList = components["schemas"]["DigestListRead"];
export type Preference = components["schemas"]["PreferenceRead"];
export type AuthSession = components["schemas"]["AuthSessionRead"];

/** Error carrying the HTTP status so callers can branch on 401/503. */
export class AccountApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function accountFetch<T>(
  path: string,
  init: RequestInit = {},
): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    credentials: "include",
    ...init,
    headers: {
      ...(init.body ? { "Content-Type": "application/json" } : {}),
      ...(init.headers ?? {}),
    },
  });
  if (!response.ok) {
    throw new AccountApiError(
      response.status,
      `Account request failed: ${response.status}`,
    );
  }
  return (await response.json()) as T;
}

export function requestMagicLink(
  email: string,
  redirectPath?: string,
): Promise<{ accepted: boolean }> {
  return accountFetch("/auth/magic-link/request", {
    method: "POST",
    body: JSON.stringify({
      email,
      ...(redirectPath ? { redirect_path: redirectPath } : {}),
    }),
  });
}

export function verifyMagicLink(token: string): Promise<AuthSession> {
  return accountFetch("/auth/magic-link/verify", {
    method: "POST",
    body: JSON.stringify({ token }),
  });
}

export function logout(): Promise<{ signed_out: boolean }> {
  return accountFetch("/auth/logout", { method: "POST" });
}

export function getMe(): Promise<AccountUser> {
  return accountFetch("/me");
}

export function getSavedMedia(): Promise<SavedMediaList> {
  return accountFetch("/me/saves");
}

export function removeSavedMedia(
  mediaId: number,
): Promise<{ deleted: boolean }> {
  return accountFetch(`/me/saves/${mediaId}`, { method: "DELETE" });
}

export function getFollowedPodcasts(): Promise<FollowedPodcastList> {
  return accountFetch("/me/follows");
}

export function unfollowPodcast(
  podcastId: number,
): Promise<{ deleted: boolean }> {
  return accountFetch(`/me/follows/${podcastId}`, { method: "DELETE" });
}

export function getAlertRules(): Promise<AlertRuleList> {
  return accountFetch("/me/alerts");
}

export function setAlertRuleEnabled(
  ruleId: number,
  enabled: boolean,
): Promise<AlertRule> {
  return accountFetch(`/me/alerts/${ruleId}`, {
    method: "PATCH",
    body: JSON.stringify({ enabled }),
  });
}

export function deleteAlertRule(ruleId: number): Promise<{ deleted: boolean }> {
  return accountFetch(`/me/alerts/${ruleId}`, { method: "DELETE" });
}

export function getDigests(): Promise<DigestList> {
  return accountFetch("/me/digests");
}

export function getPreferences(): Promise<Preference> {
  return accountFetch("/me/preferences");
}

export function updatePreferences(input: {
  digest_enabled: boolean;
  digest_frequency: "immediate" | "daily" | "weekly";
}): Promise<Preference> {
  return accountFetch("/me/preferences", {
    method: "PATCH",
    body: JSON.stringify(input),
  });
}

export function exportAccountData(): Promise<Record<string, unknown>> {
  return accountFetch("/me/export");
}

export function deleteAccount(): Promise<{ signed_out: boolean }> {
  return accountFetch("/me", { method: "DELETE" });
}
