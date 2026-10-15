"""Role checks for the separate Artist and Fan authentication models."""

from functools import wraps
from typing import Any, Callable

from flask_login import current_user

from app.models.artist import Artist
from app.utils.responses import error


def artist_required(view: Callable[..., Any]) -> Callable[..., Any]:
    """Require an Artist after login_required, before entering a mutation."""
    @wraps(view)
    def wrapped(*args: Any, **kwargs: Any) -> Any:
        """Reject other user types even when their numeric IDs match."""
        if not isinstance(current_user, Artist):
            return error("Only artists can modify artist resources.", 403)
        return view(*args, **kwargs)

    return wrapped
