# SPDX-License-Identifier: MIT OR Apache-2.0
"""Kernel-held spool exclusion plus a fresh acknowledgement from the live daemon.

No database, application configuration or secret is imported by this module.
A crashed controller leaves its pause request behind: resumption requires the
installation's separately fenced recovery procedure, never a timeout cleanup.
"""

from __future__ import annotations

import fcntl
import os
import re
import stat
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from offerloom.execution_guard import ExecutionGuard
from offerloom.storage import atomic_file, directory, read_file

_LOCK = ".maintenance.lock"
_REQUEST = ".maintenance.request"
_ACK = ".maintenance.ack"
_PROTOCOL = b"offerloom-spool-maintenance/v1\n"
_NONCE = re.compile(rb"[0-9a-f]{32}\Z")


class SpoolBusy(BlockingIOError):
    pass


def _request(fd: int) -> bytes | None:
    try:
        value = read_file(fd, _REQUEST, 128)
    except FileNotFoundError:
        return None
    if not value.startswith(_PROTOCOL) or not _NONCE.fullmatch(value[len(_PROTOCOL) :]):
        raise ValueError("invalid_spool_maintenance_request")
    return value


def _open_lock(fd: int) -> int:
    lock = os.open(_LOCK, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600, dir_fd=fd)
    info = os.fstat(lock)
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) & 0o077:
        os.close(lock)
        raise ValueError("invalid_spool_maintenance_lock")
    return lock


@contextmanager
def shared_spool(fd: int) -> Iterator[int]:
    """Cover all spool work; supervisors pass this descriptor to their child.

    Close, never LOCK_UN: a surviving child must retain exclusion after its
    daemon exits, until the kernel closes the last inherited descriptor.
    """
    if _request(fd) is not None:
        raise SpoolBusy("spool_maintenance_active")
    lock = _open_lock(fd)
    try:
        try:
            fcntl.flock(lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SpoolBusy("spool_maintenance_active") from None
        if _request(fd) is not None:
            raise SpoolBusy("spool_maintenance_active")
        yield lock
    finally:
        os.close(lock)


def acknowledge_pause(fd: int) -> bool:
    """Called only between jobs, after every supervised child has been reaped."""
    request = _request(fd)
    if request is None:
        return False
    try:
        acknowledged = read_file(fd, _ACK, 128)
    except FileNotFoundError:
        acknowledged = None
    if acknowledged != request:
        atomic_file(fd, _ACK, request)
    return True


class PausedSpool:
    def __init__(self, fd: int, request: bytes) -> None:
        self._fd = fd
        self._request = request
        self.active = True

    @property
    def nonce(self) -> str:
        if not self.active or _request(self._fd) != self._request:
            raise ValueError("spool_maintenance_fence_lost")
        if read_file(self._fd, _ACK, 128) != self._request:
            raise ValueError("spool_maintenance_ack_lost")
        return self._request[len(_PROTOCOL) :].decode("ascii")

    def resume(self) -> None:
        """The caller must first commit its fenced maintenance settlement."""
        if not self.active or _request(self._fd) != self._request:
            raise ValueError("spool_maintenance_fence_lost")
        os.unlink(_ACK, dir_fd=self._fd)
        os.unlink(_REQUEST, dir_fd=self._fd)
        os.fsync(self._fd)


@contextmanager
def pause_spool(
    spool: Path, *, timeout: float = 60.0, recovery_guard: ExecutionGuard | None = None
) -> Iterator[PausedSpool]:
    """Require both kernel exclusion and an exact fresh idle acknowledgement.

    Holding an empty spool, a stale receipt or an old/nonparticipating daemon
    is not sufficient. Timeout does not remove a published pause request.
    The caller supplies its remaining installation-wide drain deadline.
    """
    if not 0 < timeout <= 60:
        raise ValueError("invalid_spool_maintenance_timeout")
    deadline = time.monotonic() + timeout
    if recovery_guard is not None:
        if not recovery_guard.exclusive:
            raise ValueError("offline_recovery_exclusion_required")
        recovery_guard.verify()
    with directory(spool) as fd:
        lock = _open_lock(fd)
        paused: PausedSpool | None = None
        try:
            while True:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise SpoolBusy("spool_maintenance_timeout") from None
                    time.sleep(min(0.05, max(0, deadline - time.monotonic())))
            if _request(fd) is not None and recovery_guard is None:
                raise SpoolBusy("spool_maintenance_recovery_required")
            request = _PROTOCOL + uuid4().hex.encode("ascii")
            atomic_file(fd, _REQUEST, request)
            while time.monotonic() < deadline:
                try:
                    acknowledgement = read_file(fd, _ACK, 128)
                except FileNotFoundError:
                    acknowledgement = None
                if acknowledgement == request:
                    paused = PausedSpool(fd, request)
                    yield paused
                    return
                time.sleep(min(0.05, max(0, deadline - time.monotonic())))
            raise SpoolBusy("spool_maintenance_ack_timeout")
        finally:
            if paused is not None:
                paused.active = False
            os.close(lock)
