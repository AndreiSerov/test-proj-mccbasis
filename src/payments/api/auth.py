import secrets
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request, status


async def require_api_key(
    request: Request,
    x_api_key: Annotated[str | None, Header()] = None,
) -> None:
    expected: str = request.app.state.settings.api_key
    # compare_digest on bytes: str comparison rejects non-ASCII input.
    if x_api_key is None or not secrets.compare_digest(
        x_api_key.encode(), expected.encode()
    ):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or missing API key")


ApiKeyAuth = Depends(require_api_key)
