"""Cross-process serialization for Galaxy Buds profile operations."""

from __future__ import annotations

import fcntl
import os
import time
from contextlib import contextmanager
from functools import wraps
from pathlib import Path
from typing import Callable, Iterator, ParamSpec, TypeVar


P = ParamSpec("P")
R = TypeVar("R")


class BudsOperationBusy(OSError):
    pass


def default_operation_lock_path() -> Path:
    cache_home = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return cache_home / "galaxybridge" / "buds-operation.lock"


@contextmanager
def buds_operation_lock(timeout: float = 20.0) -> Iterator[None]:
    """Serialize SMEP and regular-profile operations across processes."""
    path = default_operation_lock_path()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    deadline = time.monotonic() + timeout
    try:
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise BudsOperationBusy("another GalaxyBridge Buds operation is still active")
                time.sleep(0.1)
        yield
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def serialized_buds_operation(function: Callable[P, R]) -> Callable[P, R]:
    @wraps(function)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        with buds_operation_lock():
            return function(*args, **kwargs)

    return wrapper
