# SPDX-License-Identifier: MIT OR Apache-2.0
"""Bounded real worker process; PostgreSQL is the only execution authority."""

import signal
import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from threading import Event
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, OperationalError

from offerloom import documents, imports, local_model, sources, suitability
from offerloom.commands import canonical_json, lock_installation
from offerloom.config import Settings
from offerloom.db import Database
from offerloom.import_parser import ImportFailure
from offerloom.problems import Problem
from offerloom.render_service import RenderClient, RenderFailure
from offerloom.storage import BlobStore
from offerloom.workflows import (
    Claim,
    assert_fence,
    claim,
    claim_outbox,
    deliver_outbox,
    fail,
    heartbeat,
    publish,
    reap,
)


class LeaseLost(RuntimeError):
    pass


@dataclass
class Running:
    task: Claim
    future: Future[None]
    interrupted: Event


class Worker:
    CONCURRENCY = 2
    HEARTBEAT_SECONDS = 15

    def __init__(self, db: Database, settings: Settings) -> None:
        self.db = db
        self.settings = settings
        self.id = uuid4()
        self.stopping = Event()

    def stop(self, signum: int = 0, frame: object = None) -> None:
        del signum, frame
        self.stopping.set()

    def _wait(self, seconds: int, interrupted: Event) -> None:
        if interrupted.wait(seconds) or self.stopping.is_set():
            raise LeaseLost("execution interrupted")

    def _stage(self, task: Claim, checkpoint: dict[str, object]) -> None:
        with self.db.transaction() as conn:
            assert_fence(conn, task)
            conn.execute(
                text("""INSERT INTO offerloom.workflow_steps(owner_id,workflow_run_id,step_key,attempt,fence,state,checkpoint,completed_at)
                VALUES(:owner,:id,'staged',:attempt,:fence,'succeeded',CAST(:checkpoint AS jsonb),clock_timestamp())"""),
                {
                    "owner": task.owner_id,
                    "id": task.id,
                    "attempt": task.attempt,
                    "fence": task.fence,
                    "checkpoint": canonical_json(checkpoint),
                },
            )

    def _execute(self, task: Claim, interrupted: Event) -> None:
        try:
            if task.input.get("schema_version") != 1:
                raise Problem(422, "unsupported_workflow_input_version")
            scenario = task.input.get("scenario", "normal")
            if scenario != "normal" and self.settings.mode != "benchmark":
                raise Problem(422, "benchmark_workflow_forbidden")
            delay_value = task.input.get("delay_seconds", 0)
            if not isinstance(delay_value, int) or not 0 <= delay_value <= 120:
                raise Problem(422, "invalid_workflow_delay")
            delay = delay_value or (120 if scenario in {"slow", "stage_pause"} else 0)
            if scenario == "slow":
                self._wait(delay, interrupted)
            if scenario == "poison":
                # Two identical normalized deterministic failures form visible poison.
                raise RenderFailure("benchmark_poison")
            if task.kind == "profile_import":
                imports.execute_import(self.db, self.settings, task, interrupted)
            elif task.kind == "source_refresh":
                sources.execute_refresh(self.db, task, interrupted)
            elif task.kind in {"posting_extract", "matching"}:
                suitability.execute_model(self.db, self.settings, task, interrupted)
            elif task.kind == "document_generate":
                if self.settings.mode != "benchmark":
                    raise Problem(422, "no_production_generation_provider")
                profile_revision_id = UUID(str(task.input["profile_revision_id"]))
                with self.db.transaction() as conn:
                    prepared = documents.prepare_synthetic(conn, task.owner_id, profile_revision_id)
                if scenario == "stage_pause":
                    self._stage(
                        task, {"phase": "prepared", "profile_revision_id": str(profile_revision_id)}
                    )
                    self._wait(delay, interrupted)
                if interrupted.is_set():
                    raise LeaseLost("lease lost")
                with self.db.transaction() as conn:
                    publish(
                        conn,
                        task,
                        lambda active: documents.publish_document(
                            active, task.owner_id, task.id, prepared
                        ),
                    )
            elif task.kind == "pdf_render":
                document_id = UUID(str(task.input["document_id"]))
                revision_id = UUID(str(task.input["revision_id"]))
                with self.db.transaction() as conn:
                    render = documents.prepare_render(conn, task.owner_id, document_id, revision_id)
                # Spool processing and private content-addressed blob I/O never hold DB locks.
                pdf = RenderClient(self.settings.renderer_spool).render(render.body)
                blob = BlobStore(self.settings.blob_root).put(task.owner_id, pdf)
                if scenario == "stage_pause":
                    self._stage(task, {"phase": "blob_staged", "revision_id": str(revision_id)})
                    self._wait(delay, interrupted)
                if interrupted.is_set():
                    raise LeaseLost("lease lost")
                with self.db.transaction() as conn:
                    publish(
                        conn,
                        task,
                        lambda active: documents.publish_artifact(
                            active, task.owner_id, task.id, render, blob
                        ),
                    )
            else:
                raise Problem(422, "unsupported_workflow_kind")
        except LeaseLost:
            return
        except Problem as exc:
            self._record_failure(task, exc.code, False)
        except RenderFailure as exc:
            self._record_failure(task, exc.code, exc.code in {"render_timeout", "benchmark_poison"})
        except local_model.ModelFailure as exc:
            # Shutdown/lease-loss Events are execution interruption, not an owner
            # cancellation command or a new terminal model failure. PostgreSQL's
            # reaper retains authority over cancellation, deadlines and budgets.
            if not interrupted.is_set() and exc.code != "model_interrupted":
                self._record_failure(task, exc.code, exc.retryable)
        except ImportFailure as exc:
            if exc.code != "import_interrupted":
                self._record_failure(
                    task,
                    exc.code,
                    task.attempt < 3
                    and exc.code in {"import_timeout", "import_parser_unavailable"},
                )
        except (OperationalError, OSError):
            self._record_failure(task, "temporary_local_unavailable", True)
        except (KeyError, ValueError, TypeError):
            self._record_failure(task, "invalid_workflow_input", False)
        except DBAPIError:
            # Fencing failure is not a new task error; its current owner decides outcome.
            return
        except Exception:
            # Never serialize exception messages or private input into status/logs.
            self._record_failure(task, "worker_internal_error", False)

    def _record_failure(self, task: Claim, code: str, transient: bool) -> None:
        try:
            with self.db.transaction() as conn:
                fail(conn, task, code, transient)
        except (DBAPIError, Problem):
            # DB outage or superseding fence: reaper retains durable recovery authority.
            return

    def _pulse(self) -> None:
        with self.db.transaction() as conn:
            lock_installation(conn)
            conn.execute(
                text("""INSERT INTO offerloom.worker_heartbeats(id,concurrency) VALUES(:id,:slots)
                ON CONFLICT(id) DO UPDATE SET heartbeat_at=clock_timestamp()"""),
                {"id": self.id, "slots": self.CONCURRENCY},
            )

    def run(self) -> None:
        signal.signal(signal.SIGTERM, self.stop)
        signal.signal(signal.SIGINT, self.stop)
        running: list[Running] = []
        last_heartbeat = 0.0
        with ThreadPoolExecutor(
            max_workers=self.CONCURRENCY, thread_name_prefix="offerloom-work"
        ) as executor:
            while not self.stopping.is_set():
                try:
                    now = time.monotonic()
                    if now - last_heartbeat >= self.HEARTBEAT_SECONDS:
                        self._pulse()
                        with self.db.transaction() as conn:
                            reap(conn)
                        for active in running:
                            with self.db.transaction() as conn:
                                if not heartbeat(conn, active.task):
                                    active.interrupted.set()
                        last_heartbeat = now
                    running = [active for active in running if not active.future.done()]
                    while len(running) < self.CONCURRENCY:
                        with self.db.transaction() as conn:
                            task = claim(conn, self.id)
                        if task is None:
                            break
                        interrupted = Event()
                        future = executor.submit(self._execute, task, interrupted)
                        running.append(Running(task, future, interrupted))
                    with self.db.transaction() as conn:
                        event = claim_outbox(conn, self.id)
                    if event is not None:
                        with self.db.transaction() as conn:
                            deliver_outbox(conn, event)
                except (DBAPIError, Problem):
                    # Stop renewing computation on DB loss; expired leases are recoverable.
                    for active in running:
                        active.interrupted.set()
                self.stopping.wait(0.5)
            for active in running:
                active.interrupted.set()
        with self.db.transaction() as conn:
            lock_installation(conn)
            conn.execute(
                text("DELETE FROM offerloom.worker_heartbeats WHERE id=:id"), {"id": self.id}
            )


def main(settings: Settings) -> None:
    db = Database(settings)
    try:
        if not db.ready():
            raise RuntimeError("database migrations are not ready")
        Worker(db, settings).run()
    finally:
        db.close()
