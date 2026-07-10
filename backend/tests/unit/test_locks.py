"""Non-blocking Redis lock: single acquisition, skip-when-held, token-checked
release, TTL crash backstop."""

import fakeredis

from app.core.locks import redis_lock


def _redis():
    return fakeredis.FakeRedis(decode_responses=True)


class TestRedisLock:
    def test_acquires_and_releases(self):
        client = _redis()
        with redis_lock(client, "alerts:evaluate", ttl_seconds=60) as acquired:
            assert acquired is True
            assert client.get("lock:alerts:evaluate") is not None
        assert client.get("lock:alerts:evaluate") is None  # released on exit

    def test_second_holder_is_told_to_skip(self):
        client = _redis()
        with redis_lock(client, "alerts:evaluate", ttl_seconds=60) as first:
            assert first is True
            with redis_lock(client, "alerts:evaluate", ttl_seconds=60) as second:
                assert second is False
            # the skipped runner must NOT have released the holder's lock
            assert client.get("lock:alerts:evaluate") is not None

    def test_ttl_is_set_as_crash_backstop(self):
        client = _redis()
        with redis_lock(client, "bank:maturities", ttl_seconds=300):
            ttl = client.ttl("lock:bank:maturities")
            assert 0 < ttl <= 300

    def test_release_survives_a_body_exception(self):
        client = _redis()
        try:
            with redis_lock(client, "exchange:sync:1", ttl_seconds=60) as acquired:
                assert acquired
                raise RuntimeError("sync blew up")
        except RuntimeError:
            pass
        assert client.get("lock:exchange:sync:1") is None

    def test_stale_holder_cannot_release_a_successor(self):
        client = _redis()
        with redis_lock(client, "alerts:evaluate", ttl_seconds=60) as acquired:
            assert acquired
            # simulate the TTL expiring mid-run and another runner acquiring
            client.set("lock:alerts:evaluate", "someone-else", ex=60)
        # exit must not have deleted the successor's lock (token mismatch)
        assert client.get("lock:alerts:evaluate") == "someone-else"

    def test_locks_are_independent_by_name(self):
        client = _redis()
        with redis_lock(client, "exchange:sync:1", ttl_seconds=60) as a:
            with redis_lock(client, "exchange:sync:2", ttl_seconds=60) as b:
                assert a is True and b is True
