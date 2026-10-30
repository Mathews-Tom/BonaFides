# SPDX-License-Identifier: MIT OR Apache-2.0
"""Exercise transport deadlines with a controlled clock, not wall-clock sleeps."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import cast

import pytest

from bonafides import relay


@dataclass
class _Clock:
    now: float

    def time(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class _Reader(asyncio.StreamReader):
    def __init__(self) -> None:
        super().__init__()
        self.started: asyncio.Queue[None] = asyncio.Queue()

    async def read(self, n: int = -1) -> bytes:
        self.started.put_nowait(None)
        return await super().read(n)


class _Writer:
    def __init__(self, *, blocked: bool = False) -> None:
        self.written: list[bytes] = []
        self.closed = False
        self.eof = False
        self.blocked = blocked
        self.draining: asyncio.Queue[None] = asyncio.Queue()
        self.release = asyncio.Event()

    def write(self, data: bytes) -> None:
        self.written.append(data)

    async def drain(self) -> None:
        self.draining.put_nowait(None)
        if self.blocked:
            await self.release.wait()

    def can_write_eof(self) -> bool:
        return True

    def write_eof(self) -> None:
        self.eof = True

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        pass


@dataclass
class _Connection:
    clock: _Clock
    client: _Reader
    upstream: _Reader
    client_writer: _Writer
    upstream_writer: _Writer
    task: asyncio.Task[None]

    async def send(self, reader: _Reader, data: bytes) -> None:
        reader.feed_data(data)
        next_read = asyncio.create_task(reader.started.get())
        try:
            done, _ = await asyncio.wait(
                (self.task, next_read), return_when=asyncio.FIRST_COMPLETED
            )
            assert self.task not in done, "relay expired while forwarding supported traffic"
            await next_read
        finally:
            next_read.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await next_read


@asynccontextmanager
async def _connection(
    monkeypatch: pytest.MonkeyPatch, *, api: bool = True, blocked: bool = False
) -> AsyncIterator[_Connection]:
    loop = asyncio.get_running_loop()
    clock = _Clock(loop.time())
    monkeypatch.setattr(loop, "time", clock.time)
    client, upstream = _Reader(), _Reader()
    client_writer, upstream_writer = _Writer(), _Writer(blocked=blocked)

    async def connect(*args: object, **kwargs: object) -> tuple[_Reader, asyncio.StreamWriter]:
        return upstream, cast(asyncio.StreamWriter, upstream_writer)

    monkeypatch.setattr(asyncio, "open_connection", connect)
    task = asyncio.create_task(
        relay.forward(
            client,
            cast(asyncio.StreamWriter, client_writer),
            ("private-service", 8000),
            api=api,
        )
    )
    await asyncio.gather(client.started.get(), upstream.started.get())
    try:
        yield _Connection(clock, client, upstream, client_writer, upstream_writer, task)
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


def test_api_delayed_headers_on_first_and_reused_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def exercise() -> None:
        async with _connection(monkeypatch) as connection:
            for request in (
                b"GET /health HTTP/1.1\r\nHost: local\r\n\r\n",
                b"GET /api/v1/portable-exports/owner-export/download HTTP/1.1\r\n"
                b"Host: local\r\n\r\n",
            ):
                await connection.send(connection.client, request)
                # Full integrity verification can outlast both old relay limits.
                connection.clock.advance(relay.SESSION_SECONDS + 1)
                response = b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nOK"
                await connection.send(connection.upstream, response)
            assert connection.upstream_writer.written == [
                b"GET /health HTTP/1.1\r\nHost: local\r\n\r\n",
                b"GET /api/v1/portable-exports/owner-export/download HTTP/1.1\r\n"
                b"Host: local\r\n\r\n",
            ]
            assert connection.client_writer.written == [response, response]
            connection.client.feed_eof()
            connection.upstream.feed_eof()
            await connection.task
            assert connection.client_writer.eof and connection.upstream_writer.eof
            assert connection.client_writer.closed and connection.upstream_writer.closed

    asyncio.run(exercise())


@pytest.mark.parametrize("direction", ["upload", "download"])
def test_api_activity_in_either_direction_renews_shared_idle_budget(
    monkeypatch: pytest.MonkeyPatch, direction: str
) -> None:
    async def exercise() -> None:
        async with _connection(monkeypatch) as connection:
            source = connection.client if direction == "upload" else connection.upstream
            destination = (
                connection.upstream_writer if direction == "upload" else connection.client_writer
            )
            # Cross several complete API idle budgets while the other side stays quiet.
            # There must be no independent-direction or absolute API session expiry.
            for _ in range(6):
                connection.clock.advance(relay.API_IDLE_SECONDS / 2)
                await connection.send(source, b"bounded archive chunk")
            assert destination.written == [b"bounded archive chunk"] * 6
            connection.clock.advance(relay.API_IDLE_SECONDS + 1)
            await connection.task
            assert connection.client_writer.closed and connection.upstream_writer.closed

    asyncio.run(exercise())


@pytest.mark.parametrize("api", [False, True])
def test_silent_connection_expires(monkeypatch: pytest.MonkeyPatch, api: bool) -> None:
    async def exercise() -> None:
        async with _connection(monkeypatch, api=api) as connection:
            connection.clock.advance((relay.API_IDLE_SECONDS if api else relay.IDLE_SECONDS) + 1)
            await connection.task
            assert connection.client_writer.closed and connection.upstream_writer.closed
            assert connection.client_writer.written == connection.upstream_writer.written == []

    asyncio.run(exercise())


def test_db_retains_independent_direction_idle_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    async def exercise() -> None:
        async with _connection(monkeypatch, api=False) as connection:
            connection.clock.advance(relay.IDLE_SECONDS / 2)
            await connection.send(connection.upstream, b"database response")
            connection.clock.advance(relay.IDLE_SECONDS / 2 + 1)
            await connection.task
            assert connection.client_writer.written == [b"database response"]
            assert connection.client_writer.closed and connection.upstream_writer.closed

    asyncio.run(exercise())


def test_db_retains_absolute_session_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    async def exercise() -> None:
        async with _connection(monkeypatch, api=False) as connection:
            step = relay.IDLE_SECONDS / 2
            for _ in range(int(relay.SESSION_SECONDS / step) - 1):
                connection.clock.advance(step)
                await connection.send(connection.client, b"query")
                await connection.send(connection.upstream, b"response")
            connection.clock.advance(step + 1)
            await connection.task
            assert connection.client_writer.closed and connection.upstream_writer.closed

    asyncio.run(exercise())


def test_api_stalled_write_retains_short_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    async def exercise() -> None:
        async with _connection(monkeypatch, blocked=True) as connection:
            connection.client.feed_data(b"body cannot drain")
            await connection.upstream_writer.draining.get()
            connection.clock.advance(relay.IDLE_SECONDS + 1)
            await connection.task
            assert connection.upstream_writer.written == [b"body cannot drain"]
            assert connection.client_writer.closed and connection.upstream_writer.closed

    asyncio.run(exercise())


def test_api_upstream_connect_remains_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    async def exercise() -> None:
        loop = asyncio.get_running_loop()
        clock = _Clock(loop.time())
        monkeypatch.setattr(loop, "time", clock.time)
        connecting, release = asyncio.Event(), asyncio.Event()
        writer = _Writer()

        async def connect(*args: object, **kwargs: object) -> None:
            connecting.set()
            await release.wait()

        monkeypatch.setattr(asyncio, "open_connection", connect)
        task = asyncio.create_task(
            relay.forward(
                _Reader(), cast(asyncio.StreamWriter, writer), ("private-service", 8000), api=True
            )
        )
        await connecting.wait()
        clock.advance(6)
        await task
        assert writer.closed

    asyncio.run(exercise())
