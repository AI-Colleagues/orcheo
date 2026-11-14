"""The in-process credential lock shared by vaults without a locking store."""

from __future__ import annotations
from threading import Event, Thread
import pytest
from orcheo.vault import InMemoryCredentialVault


def _hold(
    vault: InMemoryCredentialVault, key: str, held: Event, release: Event
) -> None:
    with vault.credential_lock(key):
        held.set()
        assert release.wait(5)


def _acquire(vault: InMemoryCredentialVault, key: str, acquired: Event) -> Thread:
    def run() -> None:
        with vault.credential_lock(key):
            acquired.set()

    thread = Thread(target=run)
    thread.start()
    return thread


def test_the_same_key_is_exclusive_and_other_keys_are_not() -> None:
    vault = InMemoryCredentialVault()
    held, release = Event(), Event()
    holder = Thread(target=_hold, args=(vault, "mailbox:1", held, release))
    holder.start()
    assert held.wait(5)

    other_key, same_key = Event(), Event()
    other = _acquire(vault, "mailbox:2", other_key)
    contender = _acquire(vault, "mailbox:1", same_key)
    other.join(5)
    assert other_key.is_set()
    assert not same_key.wait(0.2)

    release.set()
    holder.join(5)
    contender.join(5)
    assert same_key.is_set()


def test_a_thread_can_reenter_its_own_key_and_nest_other_keys() -> None:
    vault = InMemoryCredentialVault()
    with vault.credential_lock("a"):
        with vault.credential_lock("a"):
            with vault.credential_lock("b"):
                assert set(vault._credential_locks) == {"a", "b"}  # noqa: SLF001
        assert set(vault._credential_locks) == {"a"}  # noqa: SLF001


def test_locks_are_released_and_forgotten_even_after_a_failure() -> None:
    vault = InMemoryCredentialVault()
    for number in range(50):
        with vault.credential_lock(f"email-schedule:{number}"):
            pass
    with pytest.raises(RuntimeError, match="boom"):
        with vault.credential_lock("failing"):
            raise RuntimeError("boom")
    assert vault._credential_locks == {}  # noqa: SLF001
    with vault.credential_lock("failing"):
        pass
