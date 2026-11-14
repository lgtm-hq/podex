"""OAuth state cookie policy shared by callback success and error responses."""

from fastapi import Request, Response

WORKOS_STATE_COOKIE = "podex_oauth_state"
WORKOS_CALLBACK_ROUTE_NAME = "complete_workos_callback"


def clear_failed_callback_state(*, request: Request, response: Response) -> None:
    """Expire state on every terminal callback failure, including exchange 502s."""
    route = request.scope.get("route")
    if (
        getattr(route, "name", None) == WORKOS_CALLBACK_ROUTE_NAME
        and response.status_code >= 400
    ):
        # Owner policy: 502 also consumes state and requires a fresh sign-in.
        # To allow retry, exclude 502 here; all other failure paths stay covered.
        response.delete_cookie(key=WORKOS_STATE_COOKIE, path="/")
