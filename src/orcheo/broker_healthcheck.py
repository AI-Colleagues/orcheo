"""Check broker reachability from a lean stack container."""

from __future__ import annotations
import os
import sys
from redis import Redis


def main() -> int:
    """Return a failing exit status when Redis cannot be reached by service name."""
    redis = Redis.from_url(
        os.getenv("REDIS_URL", "redis://localhost:6379/0"),
        socket_connect_timeout=2,
        socket_timeout=2,
    )
    try:
        return 0 if redis.ping() else 1
    except Exception:
        return 1
    finally:
        redis.close()


if __name__ == "__main__":
    sys.exit(main())
