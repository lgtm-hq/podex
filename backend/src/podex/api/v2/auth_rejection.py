"""The sign-in callback's handled response for a rejected authorization code.

When WorkOS answers that the authorization code is invalid, expired or
already used, the callback raises :class:`RejectedSignInCodeError` instead of
an ``HTTPException``. It is rendered exactly like the callback's generic
failed-exchange 502 (same handler, same body and headers), but it is a
dedicated type so the error reporter can tell it apart from every other 502:
any caller can trigger it by replaying a code, so it is logged, not reported.

The type has no HTTP ``status_code`` attribute, so the reporter's framework
integration does not capture it as a server error; the reporter's
``before_send`` hook additionally drops it by type should it be captured in
another way. Construction requires a token private to this module and the
class cannot be subclassed; :func:`rejected_sign_in_code` is the only
constructor and only the callback calls it (enforced by a test).
"""

from fastapi import HTTPException, Request, Response, status

from podex.api.v2.errors import http_exception_handler

SIGN_IN_FAILED = "Unable to complete hosted sign-in"
"""Problem detail for any failed code exchange, rejected code included."""

_TOKEN = object()


class RejectedSignInCodeError(Exception):
    """The provider rejected the sign-in code; render a 502, do not report.

    Args:
        token: The module-private construction token.

    Raises:
        TypeError: When constructed without the module-private token.
    """

    def __init__(self, *, token: object) -> None:
        if token is not _TOKEN:
            msg = "use rejected_sign_in_code(); only the sign-in callback raises this"
            raise TypeError(msg)
        super().__init__(SIGN_IN_FAILED)

    def __init_subclass__(cls, **kwargs: object) -> None:
        msg = "RejectedSignInCodeError cannot be subclassed"
        raise TypeError(msg)


def rejected_sign_in_code() -> RejectedSignInCodeError:
    """Create the handled rejection; called only by the sign-in callback.

    Returns:
        RejectedSignInCodeError: The exception to raise from the rejection.
    """
    return RejectedSignInCodeError(token=_TOKEN)


async def render_rejected_sign_in_code(request: Request, exc: Exception) -> Response:
    """Render a rejected code exactly like the generic failed-exchange 502.

    Args:
        request: The sign-in callback request.
        exc: The :class:`RejectedSignInCodeError` being handled.

    Returns:
        Response: The same problem response the generic 502 produces.
    """
    del exc
    response: Response = await http_exception_handler(
        request,
        HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=SIGN_IN_FAILED),
    )
    return response
