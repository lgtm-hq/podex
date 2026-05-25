# Podex Pre-Launch Legal Review Brief

Status: prepared for external counsel review; no legal sign-off recorded.

## Review Requested

Before enabling paid accounts, Podex requests written counsel review of:

- Copyright and fair-use posture for extracting structured mentions and short context snippets from podcast transcripts.
- DMCA/takedown and creator opt-out intake, response timing, suppression scope, and recordkeeping.
- Raw transcript acquisition, encrypted artifact retention, calibration sampling, purge, and re-acquisition policy.
- Account data handling, passwordless email authentication, digest delivery, retention, and data-subject requests.
- Public Terms of Service and Privacy Policy language under `frontend/src/pages/legal/`.
- Hosted subscription positioning, billing disclosures, cancellation/refund requirements, and quota messaging.

## Product Facts for Counsel

- Public browsing exposes catalog metadata, source episode links, timestamps, mentions, and limited context rather than full raw transcripts.
- Raw transcript artifacts are ops-only and are subject to lifecycle evaluation, encrypted storage adapters, purge records, and source-level creator opt-out.
- Public takedown requests enter an operator workflow capable of suppressing raw artifacts, derivatives, mentions, and search projections with audit logging.
- Accounts use emailed magic links and revocable session cookies. Authenticated users may save references, follow podcast sources, create alert rules, and receive email digests.
- Policy pages were implemented on May 24, 2026 and are drafts pending review.

## Evidence Package

| Area | Implementation Evidence |
| --- | --- |
| Terms draft | `frontend/src/pages/legal/terms.astro` |
| Privacy draft | `frontend/src/pages/legal/privacy.astro` |
| Takedown API and decision flow | `backend/src/podex/services/takedown_requests.py`, `backend/src/podex/api/v2/ops.py` |
| Transcript artifact and purge controls | `backend/src/podex/services/transcript_artifacts.py`, `backend/src/podex/services/transcript_retention_commands.py` |
| Source retention/opt-out policy | `backend/src/podex/services/transcript_retention_policies.py` |
| Authentication/session handling | `backend/src/podex/services/account_auth.py`, `backend/src/podex/api/v2/auth.py` |
| Notifications/preferences | `backend/src/podex/services/account_digests.py`, `backend/src/podex/services/account_preferences.py` |

## Questions Requiring Written Advice

1. Are the proposed public snippet lengths, timestamp links, and derived entity displays supportable for the intended jurisdictions and commercial model?
2. What notice, agent registration, response timeline, counter-notice, and repeat-infringer process should the takedown workflow implement before launch?
3. Does permanent stratified retention of a small calibration sample require consent, contractual permissions, geographic limitations, or a different retention design?
4. Which privacy rights workflows, retention periods, processor disclosures, and international transfer terms are required for initial users?
5. What Terms, pricing disclosures, billing consent, renewal, cancellation, and refund language is required before accepting payment?

## Sign-Off Record

Complete this section only after receiving written advice.

| Item | Counsel/firm | Review date | Outcome | Remediation issues |
| --- | --- | --- | --- | --- |
| Copyright and transcript posture | Pending | Pending | Pending | Pending |
| Takedown and opt-out workflow | Pending | Pending | Pending | Pending |
| Privacy and account data handling | Pending | Pending | Pending | Pending |
| Terms, pricing, and paid launch | Pending | Pending | Pending | Pending |

## Launch Gate

Do not enable a paid tier or represent legal review as complete until written counsel advice is recorded above, approved policy revisions are published, and resulting remediation issues are closed or explicitly accepted.
