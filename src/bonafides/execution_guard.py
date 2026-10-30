# SPDX-License-Identifier: MIT OR Apache-2.0
"""One canonical, kernel-held actor exclusion file; never a PID/TTL inference."""

from __future__ import annotations

import fcntl
import os
import stat
from pathlib import Path
from time import monotonic, sleep
from types import TracebackType
from uuid import UUID, uuid4, uuid5

from bonafides.storage import directory

LOCK_NAME = ".execution.lock"
_PROTOCOL = b"bonafides-execution/v1\n"


class ExecutionGuard:
    """Close, never LOCK_UN: inherited descriptors must continue excluding recovery."""

    def __init__(self, root: Path, *, exclusive: bool = False, create: bool = True) -> None:
        self.root = root
        self.fd = -1
        self.exclusive = exclusive
        with directory(root, create=create) as folder:
            # Serialize first publication before any peer can acquire canonical SH.
            # Closing this directory descriptor releases only the initialization lock.
            deadline = monotonic() + 5.0
            while True:
                try:
                    fcntl.flock(folder, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if not create or monotonic() >= deadline:
                        raise
                    sleep(0.01)
            created = False
            try:
                if create:
                    try:
                        self.fd = os.open(
                            LOCK_NAME,
                            os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                            0o600,
                            dir_fd=folder,
                        )
                        created = True
                    except FileExistsError:
                        pass
                if self.fd < 0:
                    self.fd = os.open(
                        LOCK_NAME, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=folder
                    )
                info = os.fstat(self.fd)
                if (
                    not stat.S_ISREG(info.st_mode)
                    or info.st_nlink != 1
                    or stat.S_IMODE(info.st_mode) & 0o077
                ):
                    raise ValueError("invalid_execution_guard")
                fcntl.flock(
                    self.fd,
                    (fcntl.LOCK_EX if exclusive or created else fcntl.LOCK_SH) | fcntl.LOCK_NB,
                )
                if created:
                    encoded = _PROTOCOL + str(uuid4()).encode("ascii")
                    if os.write(self.fd, encoded) != len(encoded):
                        raise OSError("short_execution_guard_write")
                    os.fsync(self.fd)
                    os.fsync(folder)
                    if not exclusive:
                        fcntl.flock(self.fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
                self.identity = self.verify()
            except BaseException:
                self.close()
                raise

    def verify(self) -> UUID:
        if self.fd < 0:
            raise ValueError("execution_guard_closed")
        with directory(self.root) as folder:
            current = os.stat(LOCK_NAME, dir_fd=folder, follow_symlinks=False)
        held = os.fstat(self.fd)
        if (
            (held.st_dev, held.st_ino) != (current.st_dev, current.st_ino)
            or held.st_nlink != 1
            or not stat.S_ISREG(current.st_mode)
            or stat.S_IMODE(current.st_mode) & 0o077
        ):
            raise ValueError("execution_guard_replaced")
        encoded = os.pread(self.fd, 128, 0)
        if not encoded.startswith(_PROTOCOL):
            raise ValueError("execution_guard_protocol_invalid")
        identity = UUID(encoded[len(_PROTOCOL) :].decode("ascii"))
        if encoded != _PROTOCOL + str(identity).encode("ascii"):
            raise ValueError("execution_guard_protocol_invalid")
        # Bind SQL registrations to the actual kernel object, not merely bytes
        # that could have been copied into a new, unheld replacement lock.
        identity = uuid5(identity, f"{held.st_dev}:{held.st_ino}")
        previous = getattr(self, "identity", identity)
        if identity != previous:
            raise ValueError("execution_guard_replaced")
        return identity

    def close(self) -> None:
        if self.fd >= 0:
            os.close(self.fd)
            self.fd = -1

    def __enter__(self) -> ExecutionGuard:
        self.verify()
        return self

    def __exit__(
        self,
        kind: type[BaseException] | None,
        value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()
