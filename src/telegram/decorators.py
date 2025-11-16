from collections.abc import Awaitable, Callable
from functools import wraps
from typing import Any

from src.db import session_local


def with_repository(repository_cls: type | None = None):
    def decorator(func: Callable[..., Awaitable[Any]]):
        @wraps(func)
        async def wrapper(event, **data):
            async with session_local() as session:
                async with session.begin():
                    repo = repository_cls(session) if repository_cls else None
                    if repo is not None:
                        data["repo"] = repo
                    return await func(event, **data)

        return wrapper

    return decorator
