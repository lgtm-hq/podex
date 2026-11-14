"""OAuth state cookie policy shared by callback success and error responses."""

from fastapi import Request, Response

WORKOS_STATE_COOKIE = "podex_oauth_state"
WORKOS_CALLBACK_ROUTE_NAME = "complete_workos_callback"


def expire_workos_state(*, response: Response, secure: bool) -> None:
    """Delete state with the same attributes used when starting sign-in."""
    response.delete_cookie(
        key=WORKOS_STATE_COOKIE,
        path="/",
        domain=None,
        secure=secure,
        httponly=True,
        samesite="lax",
    )


def clear_failed_callback_state(*, request: Request, response: Response) -> None:
    """Expire consumed state on callback failures, including exchange 502s."""
    route = request.scope.get("route")
    # None means validation never consumed state; False is a consumed HTTP cookie.
    secure = getattr(request.state, "consumed_workos_state_secure", None)
    if (
        getattr(route, "name", None) == WORKOS_CALLBACK_ROUTE_NAME
        and secure is not None
        and response.status_code >= 400
    ):
        # Owner policy: 502 also consumes state and requires a fresh sign-in.
        # To allow retry, exclude 502 here; all other failure paths stay covered.
        expire_workos_state(response=response, secure=secure)
