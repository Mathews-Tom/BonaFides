# SPDX-License-Identifier: MIT OR Apache-2.0
from __future__ import annotations

import hashlib
import select
import subprocess
import sys
from pathlib import Path

import pytest
from test_import_parser import parser as parser

from offerloom.import_parser import ImportFailure, ImportParserClient
from offerloom.spool_guard import SpoolBusy, pause_spool, shared_spool
from offerloom.storage import atomic_file, directory, read_file


@pytest.mark.parametrize("stale_ack", [False, True])
def test_empty_spool_or_stale_ack_is_not_proof_of_a_live_idle_daemon(
    tmp_path: Path, stale_ack: bool
) -> None:
    with pytest.raises(SpoolBusy) as first, pause_spool(tmp_path, timeout=0.05):
        pytest.fail("An absent daemon cannot acknowledge quiescence")
    assert str(first.value) == "spool_maintenance_ack_timeout"
    with directory(tmp_path) as fd:
        request = read_file(fd, ".maintenance.request", 128)
        if stale_ack:
            atomic_file(fd, ".maintenance.ack", request)
            (tmp_path / ".maintenance.request").unlink()
    with pytest.raises(SpoolBusy) as second, pause_spool(tmp_path, timeout=0.05):
        pytest.fail("A previous acknowledgement cannot authorize this controller")
    assert str(second.value) == (
        "spool_maintenance_ack_timeout" if stale_ack else "spool_maintenance_recovery_required"
    )
    with directory(tmp_path) as fd, pytest.raises(SpoolBusy), shared_spool(fd):
        pytest.fail("An interrupted controller must leave admission closed")


def test_actual_parser_acknowledges_pause_and_resumes_only_after_explicit_release(
    parser: ImportParserClient,
) -> None:
    payload = b"Synthetic paused parser, with retained exact Unicode: caf\xc3\xa9.\n"
    before = parser.parse(payload, "text/plain")
    with pause_spool(parser.spool, timeout=5) as paused:
        with pytest.raises(ImportFailure) as rejected:
            parser.parse(b"Must not enter a paused spool", "text/plain")
        assert rejected.value.code == "import_parser_busy"
        assert list(parser.spool.glob("*.request.json")) == []
        paused.resume()
        # Removing the marker does not release the controller's kernel lock.
        with pytest.raises(ImportFailure) as still_locked:
            parser.parse(b"Still excluded", "text/plain")
        assert still_locked.value.code == "import_parser_busy"
    after = parser.parse(payload, "text/plain")
    assert after.text == before.text == payload.decode()
    assert after.source_sha256 == before.source_sha256 == hashlib.sha256(payload).hexdigest()
    with pytest.raises(ValueError):
        paused.resume()


def test_interrupted_controller_leaves_real_parser_paused(parser: ImportParserClient) -> None:
    with pause_spool(parser.spool, timeout=5):
        pass
    with pytest.raises(ImportFailure) as rejected:
        parser.parse(b"No implicit recovery", "text/plain")
    assert rejected.value.code == "import_parser_busy"
    with pytest.raises(SpoolBusy) as retry, pause_spool(parser.spool, timeout=0.1):
        pytest.fail("A new controller cannot overwrite the original recovery request")
    assert str(retry.value) == "spool_maintenance_recovery_required"


def test_surviving_child_retains_kernel_exclusion_after_parent_descriptor_closes(
    tmp_path: Path,
) -> None:
    child: subprocess.Popen[bytes] | None = None
    with directory(tmp_path) as fd:
        try:
            with shared_spool(fd) as guard:
                child = subprocess.Popen(
                    [
                        sys.executable,
                        "-c",
                        "import os,sys; os.fstat(int(sys.argv[1])); "
                        "print('inherited',flush=True); sys.stdin.buffer.read(1)",
                        str(guard),
                    ],
                    pass_fds=(guard,),
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                assert child.stdout is not None
                assert select.select([child.stdout], [], [], 5)[0], "Child startup timed out"
                assert child.stdout.readline() == b"inherited\n"
            # The parent's original shared descriptor is now closed. Its live
            # child still prevents exclusive access; lease/PID age is irrelevant.
            with pytest.raises(SpoolBusy) as blocked, pause_spool(tmp_path, timeout=0.05):
                pytest.fail("A live inherited descriptor must exclude maintenance")
            assert str(blocked.value) == "spool_maintenance_timeout"
            assert not (tmp_path / ".maintenance.request").exists()
            assert child.stdin is not None
            child.stdin.write(b"x")
            child.stdin.flush()
            _stdout, stderr = child.communicate(timeout=5)
            assert child.returncode == 0 and stderr == b""
            # Exclusive access becomes possible only after the actual process
            # exits. A new, independently required daemon ACK is still absent.
            with pytest.raises(SpoolBusy) as no_daemon, pause_spool(tmp_path, timeout=0.05):
                pytest.fail("Kernel exclusion alone cannot replace the live daemon ACK")
            assert str(no_daemon.value) == "spool_maintenance_ack_timeout"
        finally:
            if child is not None and child.poll() is None:
                child.kill()
                child.communicate(timeout=5)
