# SPDX-License-Identifier: MIT OR Apache-2.0
from __future__ import annotations

import select
import subprocess
import sys
from pathlib import Path

import pytest

from offerloom.execution_guard import LOCK_NAME, ExecutionGuard
from offerloom.spool_guard import SpoolBusy, pause_spool


def test_surviving_actual_subprocess_retains_execution_exclusion_after_parent_closes(
    tmp_path: Path,
) -> None:
    with ExecutionGuard(tmp_path) as shared:
        child = subprocess.Popen(
            [
                sys.executable,
                "-c",
                "import os,sys; os.fstat(int(sys.argv[1])); "
                "print('READY',flush=True); sys.stdin.buffer.read(1)",
                str(shared.fd),
            ],
            pass_fds=(shared.fd,),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
        )
        assert child.stdout is not None
        assert select.select([child.stdout], [], [], 5)[0]
        assert child.stdout.readline() == b"READY\n"
    try:
        with pytest.raises(BlockingIOError):
            ExecutionGuard(tmp_path, exclusive=True, create=False)
    finally:
        child.kill()
        child.communicate(timeout=5)
    with ExecutionGuard(tmp_path, exclusive=True, create=False) as exclusive:
        assert exclusive.verify() == exclusive.identity


def test_recovery_cannot_create_missing_lock_or_follow_a_replacement(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        ExecutionGuard(tmp_path, exclusive=True, create=False)
    with ExecutionGuard(tmp_path) as shared:
        encoded = (tmp_path / LOCK_NAME).read_bytes()
        (tmp_path / LOCK_NAME).unlink()
        with ExecutionGuard(tmp_path) as replacement:
            assert replacement.identity != shared.identity
            with pytest.raises(ValueError, match="execution_guard_replaced"):
                shared.verify()
        (tmp_path / LOCK_NAME).write_bytes(encoded)
        with ExecutionGuard(tmp_path, exclusive=True, create=False) as copied:
            assert copied.identity != shared.identity


def test_offline_recovery_still_requires_fresh_ack_not_a_previous_daemon_receipt(
    tmp_path: Path,
) -> None:
    spool, blob = tmp_path / "spool", tmp_path / "blobs"
    spool.mkdir()
    with ExecutionGuard(blob) as shared:
        identity = shared.identity
    with pytest.raises(SpoolBusy), pause_spool(spool, timeout=0.02):
        pytest.fail("No daemon exists")
    stale = (spool / ".maintenance.request").read_bytes()
    (spool / ".maintenance.ack").write_bytes(stale)
    with ExecutionGuard(blob, exclusive=True, create=False) as exclusive:
        assert exclusive.identity == identity
        with (
            pytest.raises(SpoolBusy, match="spool_maintenance_ack_timeout"),
            pause_spool(
                spool,
                timeout=0.02,
                recovery_guard=exclusive,
            ),
        ):
            pytest.fail("A stale daemon receipt must not authorize privileged recovery")
    assert (spool / ".maintenance.request").read_bytes() != stale


def test_concurrent_first_actors_share_initialized_identity_and_both_exclude_recovery(
    tmp_path: Path,
) -> None:
    creator_code = """
import os, sys
from pathlib import Path
from offerloom.execution_guard import ExecutionGuard, LOCK_NAME
real_open = os.open
def controlled_open(path, flags, *args, **kwargs):
    fd = real_open(path, flags, *args, **kwargs)
    if path == LOCK_NAME and flags & os.O_CREAT:
        print('CREATED', flush=True)
        sys.stdin.buffer.read(1)
    return fd
os.open = controlled_open
with ExecutionGuard(Path(sys.argv[1])) as guard:
    print(guard.identity, flush=True)
    sys.stdin.buffer.read(1)
"""
    peer_code = """
import sys
from pathlib import Path
from offerloom.execution_guard import ExecutionGuard
print('STARTED', flush=True)
with ExecutionGuard(Path(sys.argv[1])) as guard:
    print(guard.identity, flush=True)
    sys.stdin.buffer.read(1)
"""
    children: list[subprocess.Popen[bytes]] = []

    def start(code: str) -> subprocess.Popen[bytes]:
        child = subprocess.Popen(
            [sys.executable, "-c", code, str(tmp_path)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
        )
        children.append(child)
        return child

    def receive(child: subprocess.Popen[bytes]) -> bytes:
        assert child.stdout is not None
        assert select.select([child.stdout], [], [], 5)[0], "actor handshake timed out"
        value = child.stdout.readline()
        assert value, "actor exited before acquiring its initialized guard"
        return value

    def release(child: subprocess.Popen[bytes]) -> None:
        assert child.stdin is not None
        child.stdin.write(b"x")
        child.stdin.flush()

    try:
        creator = start(creator_code)
        assert receive(creator) == b"CREATED\n"
        peer = start(peer_code)
        assert receive(peer) == b"STARTED\n"
        assert peer.stdout is not None
        assert not select.select([peer.stdout], [], [], 0.2)[0]
        release(creator)
        identity = receive(creator)
        assert receive(peer) == identity
        with pytest.raises(BlockingIOError):
            ExecutionGuard(tmp_path, exclusive=True, create=False)
        release(creator)
        assert creator.wait(timeout=5) == 0
        with pytest.raises(BlockingIOError):
            ExecutionGuard(tmp_path, exclusive=True, create=False)
        release(peer)
        assert peer.wait(timeout=5) == 0
        with ExecutionGuard(tmp_path, exclusive=True, create=False) as recovered:
            assert f"{recovered.identity}\n".encode() == identity
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
            child.communicate(timeout=5)


@pytest.mark.parametrize("encoded", [b"", b"abandoned initialization", b"offerloom-execution/v1\n"])
def test_ordinary_startup_never_repairs_a_persisted_malformed_identity(
    tmp_path: Path, encoded: bytes
) -> None:
    lock = tmp_path / LOCK_NAME
    lock.write_bytes(encoded)
    lock.chmod(0o600)
    before = lock.stat()
    with pytest.raises(ValueError):
        ExecutionGuard(tmp_path)
    assert lock.read_bytes() == encoded
    assert (lock.stat().st_dev, lock.stat().st_ino) == (before.st_dev, before.st_ino)
