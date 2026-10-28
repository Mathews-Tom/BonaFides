# SPDX-License-Identifier: MIT OR Apache-2.0
"""Credential-free loopback ingress to one compile-time-selected private service."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
import sys

UPSTREAMS = {"api": ("api", 8000), "db": ("db", 5432)}
MAX_CONNECTIONS = 64
IDLE_SECONDS = 60
SESSION_SECONDS = 300
# Portable verification can be silent until the entire archive has been hashed.
# API traffic in either direction renews this budget, including on keep-alive reuse.
API_IDLE_SECONDS = 24 * 60 * 60
BUFFER_BYTES = 65536
logger = logging.getLogger(__name__)


async def pump(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    *,
    activity_timeout: asyncio.Timeout | None = None,
) -> None:
    while True:
        if activity_timeout is None:
            data = await asyncio.wait_for(reader.read(BUFFER_BYTES), IDLE_SECONDS)
        else:
            data = await reader.read(BUFFER_BYTES)
        if not data:
            break
        writer.write(data)
        await asyncio.wait_for(writer.drain(), IDLE_SECONDS)
        if activity_timeout is not None:
            activity_timeout.reschedule(asyncio.get_running_loop().time() + API_IDLE_SECONDS)
    if writer.can_write_eof():
        writer.write_eof()
        await asyncio.wait_for(writer.drain(), IDLE_SECONDS)


async def forward(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    upstream: tuple[str, int],
    *,
    api: bool = False,
) -> None:
    remote: asyncio.StreamWriter | None = None
    try:
        async with asyncio.timeout(API_IDLE_SECONDS if api else SESSION_SECONDS) as deadline:
            upstream_reader, upstream_writer = await asyncio.wait_for(
                asyncio.open_connection(*upstream, limit=BUFFER_BYTES), 5
            )
            remote = upstream_writer
            async with asyncio.TaskGroup() as group:
                # API sessions have no absolute lifetime while bytes keep flowing.
                # DB sessions retain their independent read and total-session limits.
                activity_timeout = deadline if api else None
                group.create_task(pump(reader, upstream_writer, activity_timeout=activity_timeout))
                group.create_task(pump(upstream_reader, writer, activity_timeout=activity_timeout))
    except* (OSError, TimeoutError):
        # No addresses, request bytes or credentials enter relay diagnostics.
        logger.warning("relay_connection_failed_or_expired")
    finally:
        writers = [writer] if remote is None else [writer, remote]
        for stream in writers:
            stream.close()
        for stream in writers:
            try:
                await asyncio.wait_for(stream.wait_closed(), 2)
            except (OSError, TimeoutError):
                logger.warning("relay_close_failed_or_expired")


async def serve(target: str) -> None:
    upstream = UPSTREAMS[target]
    tasks: set[asyncio.Task[None]] = set()
    stopped = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(signum, stopped.set)

    def accept(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        if len(tasks) >= MAX_CONNECTIONS:
            writer.close()
            return
        task = asyncio.create_task(forward(reader, writer, upstream, api=target == "api"))
        tasks.add(task)
        task.add_done_callback(tasks.discard)

    server = await asyncio.start_server(
        accept, "0.0.0.0", upstream[1], limit=BUFFER_BYTES, backlog=64
    )
    try:
        async with server:
            await stopped.wait()
    finally:
        server.close()
        await server.wait_closed()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        for signum in (signal.SIGTERM, signal.SIGINT):
            loop.remove_signal_handler(signum)


def main() -> None:
    if len(sys.argv) != 2 or sys.argv[1] not in UPSTREAMS:
        raise SystemExit("usage: python -m offerloom.relay {api|db}")
    logging.basicConfig(level=logging.WARNING)
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(serve(sys.argv[1]))


if __name__ == "__main__":
    main()
