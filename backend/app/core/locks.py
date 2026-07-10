"""Single-runner discipline for beat tasks: a non-blocking Redis lock.

SET NX EX acquires; release only deletes when the stored token still matches
(a Lua script makes the check-and-delete atomic), so a holder that outlived its
TTL can never release a successor's lock. Deliberately non-blocking — a beat
task that finds the lock held skips its run and lets the next tick try again;
waiting would just recreate the pile-up the lock exists to prevent.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager

from app.core.logging import get_logger

log = get_logger(__name__)

LOCK_PREFIX = "lock:"

_RELEASE_SCRIPT = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
end
return 0
"""


@contextmanager
def redis_lock(client, name: str, ttl_seconds: int) -> Iterator[bool]:
    """Yield True when this caller holds `name` for up to ttl_seconds, False when
    another runner already does (caller should skip, not wait). The TTL is the
    crash backstop: pick it >= the wrapped work's worst case."""
    key = f"{LOCK_PREFIX}{name}"
    token = uuid.uuid4().hex
    acquired = bool(client.set(key, token, nx=True, ex=ttl_seconds))
    try:
        yield acquired
    finally:
        if acquired:
            try:
                client.eval(_RELEASE_SCRIPT, 1, key, token)
            except Exception:  # noqa: BLE001 — no scripting support (test doubles)
                try:
                    value = client.get(key)
                    if value in (token, token.encode()):
                        client.delete(key)
                except Exception as exc:  # noqa: BLE001 — the TTL reclaims the lock
                    log.warning("lock_release_failed", lock=name, error=str(exc))
