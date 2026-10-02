"""Compatibility entrypoint for the lightweight broker health probe."""

from orcheo.broker_healthcheck import Redis, main


__all__ = ["Redis", "main"]


if __name__ == "__main__":
    import sys

    sys.exit(main())
