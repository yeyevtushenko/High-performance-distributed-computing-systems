import asyncio
import http.client
import json
import os
import random
import signal
import socket
import sys
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

import asyncpg
from prometheus_client import Counter, Gauge, Histogram, start_http_server

WORKER_ID = os.environ.get("WORKER_ID") or socket.gethostname()
WEB_URL = os.environ.get("WEB_URL", "http://web:8000")
CLAIM = os.environ.get("CLAIM", "skip_locked")
SAFE = os.environ.get("SAFE", "1") == "1"
WAIT = os.environ.get("WAIT", "poll")
LEASE = float(os.environ.get("LEASE_S", "5"))
T_POLL = float(os.environ.get("T_POLL_MS", "50")) / 1000
FAIL_RATE = float(os.environ.get("FAIL_RATE", "0"))
SIZE = int(os.environ.get("SLICE_SIZE", "256"))
Q_LO = float(os.environ.get("Q_LO", "0.05"))
Q_HI = float(os.environ.get("Q_HI", "0.95"))
SHUTDOWN_TIMEOUT = float(os.environ.get("SHUTDOWN_TIMEOUT_S", "25"))
LEVELS = 4096
DB_ERRORS = (OSError, asyncpg.InterfaceError, asyncpg.PostgresConnectionError, asyncpg.AdminShutdownError)

TASKS = Counter("worker_tasks", "Finished tasks", ["result"])
CLAIMS = Counter("worker_claims", "Claim attempts", ["result"])
DURATION = Histogram("worker_task_duration_seconds", "Step duration", ["step"],
                     buckets=(0.001, 0.002, 0.005, 0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1.0, 2.0, 5.0, 10.0))
IN_FLIGHT = Gauge("worker_tasks_in_flight", "Tasks being processed")

NAIVE_SELECT = "SELECT id, record_id, seed, token, attempts FROM tasks WHERE status = 'queued' ORDER BY created_at LIMIT 1"
NAIVE_UPDATE = "UPDATE tasks SET status = 'running', worker_id = $1, attempts = attempts + 1, started_at = now() WHERE id = $2"
LOCKED_CLAIM = """UPDATE tasks SET status = 'running', worker_id = $1, token = token + 1,
       lease_until = {lease}, attempts = attempts + 1, started_at = now()
WHERE id = (SELECT id FROM tasks WHERE status = 'queued'
            ORDER BY created_at LIMIT 1 FOR UPDATE{skip})
RETURNING id, record_id, seed, token, attempts"""


def log(message):
    print(f"{datetime.now(timezone.utc).isoformat(timespec='milliseconds')} {WORKER_ID} {message}", flush=True)


def process(seed):
    n = SIZE * SIZE
    pixels = [0] * n
    hist = [0] * LEVELS
    x = seed
    for i in range(n):
        x = (1103515245 * x + 12345) % 2147483648
        p = x >> 19
        pixels[i] = p
        hist[p] += 1
    p_lo = p_hi = None
    lo_target, hi_target = Q_LO * n, Q_HI * n
    cumulative = 0
    for level in range(LEVELS):
        cumulative += hist[level]
        if p_lo is None and cumulative >= lo_target:
            p_lo = level
        if cumulative >= hi_target:
            p_hi = level
            break
    width = p_hi - p_lo
    removed = 0
    total = 0
    total_sq = 0
    for p in pixels:
        if p < p_lo or p > p_hi:
            removed += 1
            continue
        v = (2 * 4095 * (p - p_lo) + width) // (2 * width) if width else 0
        total += v
        total_sq += v * v
    kept = n - removed
    mean = total / kept
    std = max(total_sq / kept - mean * mean, 0.0) ** 0.5
    return {"p_lo": p_lo, "p_hi": p_hi, "removed": removed, "mean": mean, "std": std}


class Web:
    def __init__(self, url):
        parts = urlsplit(url)
        self.host, self.port = parts.hostname, parts.port or 80
        self.conn = None

    def get(self, path):
        for attempt in range(2):
            if self.conn is None:
                self.conn = http.client.HTTPConnection(self.host, self.port, timeout=10)
            try:
                self.conn.request("GET", path)
                resp = self.conn.getresponse()
                return resp.status, resp.read()
            except (OSError, http.client.HTTPException):
                self.conn.close()
                self.conn = None
                if attempt:
                    raise


class Worker:
    def __init__(self):
        self.main = None
        self.hb = None
        self.listener = None
        self.web = Web(WEB_URL)
        self.stopping = asyncio.Event()
        self.wakeup = asyncio.Event()
        self.current = None
        if CLAIM == "naive":
            self.claim_sql = None
        else:
            lease = "now() + $2::interval" if SAFE else "NULL"
            skip = " SKIP LOCKED" if CLAIM == "skip_locked" else ""
            self.claim_sql = LOCKED_CLAIM.format(lease=lease, skip=skip)

    async def connect(self, role):
        while True:
            try:
                return await asyncpg.connect(server_settings={"application_name": f"{WORKER_ID}/{role}"})
            except DB_ERRORS + (asyncpg.PostgresError,) as e:
                log(f"connect {role} failed: {type(e).__name__}: {e}")
                await asyncio.sleep(1)

    async def on_main(self, fn):
        while True:
            try:
                return await fn(self.main)
            except DB_ERRORS as e:
                log(f"main connection lost: {type(e).__name__}: {e}")
                if not self.main.is_closed():
                    self.main.terminate()
                self.main = await self.connect("main")
                log("main connection reconnected")

    async def claim(self):
        start = time.perf_counter()
        if CLAIM == "naive":
            async def run(conn):
                row = await conn.fetchrow(NAIVE_SELECT)
                if row is not None:
                    await conn.execute(NAIVE_UPDATE, WORKER_ID, row["id"])
                return row
        else:
            args = (WORKER_ID, timedelta(seconds=LEASE)) if SAFE else (WORKER_ID,)

            async def run(conn):
                return await conn.fetchrow(self.claim_sql, *args)
        row = await self.on_main(run)
        DURATION.labels("claim").observe(time.perf_counter() - start)
        CLAIMS.labels("task" if row else "empty").inc()
        return row

    async def heartbeat(self, task, lost):
        while True:
            await asyncio.sleep(LEASE / 3)
            try:
                status = await self.hb.execute(
                    "UPDATE tasks SET lease_until = now() + $1::interval WHERE id = $2 AND token = $3",
                    timedelta(seconds=LEASE), task["id"], task["token"],
                )
            except DB_ERRORS as e:
                log(f"heartbeat connection lost: {type(e).__name__}: {e}")
                self.hb = await self.connect("hb")
                log("heartbeat connection reconnected")
                continue
            if status == "UPDATE 0":
                log(f"heartbeat updated 0 rows task={task['id']} token={task['token']}")
                lost.set()
                return

    async def store(self, task, result):
        payload = json.dumps(result)
        if not SAFE:
            async def run(conn):
                await conn.execute(
                    "INSERT INTO task_results (task_id, token, worker_id, result) VALUES ($1, $2, $3, $4)",
                    task["id"], task["token"], WORKER_ID, payload,
                )
                await conn.execute("UPDATE tasks SET status = 'done', finished_at = now() WHERE id = $1", task["id"])
                return True
        else:
            async def run(conn):
                async with conn.transaction():
                    status = await conn.execute(
                        "UPDATE tasks SET status = 'done', finished_at = now() WHERE id = $1 AND token = $2",
                        task["id"], task["token"],
                    )
                    if status == "UPDATE 0":
                        return False
                    await conn.execute(
                        "INSERT INTO task_results (task_id, token, worker_id, result) VALUES ($1, $2, $3, $4) "
                        "ON CONFLICT (task_id) DO NOTHING",
                        task["id"], task["token"], WORKER_ID, payload,
                    )
                    return True
        return await self.on_main(run)

    async def fail(self, task, reason):
        async def run(conn):
            return await conn.execute(
                "UPDATE tasks SET status = 'failed', finished_at = now() WHERE id = $1 AND token = $2",
                task["id"], task["token"],
            )
        await self.on_main(run)
        TASKS.labels("failed").inc()
        log(f"failed task={task['id']} token={task['token']} reason={reason}")

    async def execute(self, task):
        lost = asyncio.Event()
        hb = asyncio.create_task(self.heartbeat(task, lost)) if SAFE else None
        IN_FLIGHT.inc()
        try:
            start = time.perf_counter()
            status, body = await asyncio.to_thread(self.web.get, f"/donations/{task['record_id']}")
            DURATION.labels("fetch").observe(time.perf_counter() - start)
            if status != 200:
                await self.fail(task, f"fetch status {status}")
                return
            start = time.perf_counter()
            try:
                result = await asyncio.to_thread(process, task["seed"])
            except Exception as e:
                await self.fail(task, f"{type(e).__name__}: {e}")
                return
            DURATION.labels("process").observe(time.perf_counter() - start)
            if FAIL_RATE and random.random() < FAIL_RATE:
                log(f"crash task={task['id']} token={task['token']}")
                os._exit(1)
            if lost.is_set():
                TASKS.labels("lost").inc()
                log(f"lost task={task['id']} token={task['token']}")
                return
            start = time.perf_counter()
            stored = await self.store(task, result)
            DURATION.labels("store").observe(time.perf_counter() - start)
            if stored:
                TASKS.labels("done").inc()
                log(f"done task={task['id']} token={task['token']} attempts={task['attempts']}")
            else:
                TASKS.labels("lost").inc()
                log(f"store updated 0 rows task={task['id']} token={task['token']}")
        finally:
            IN_FLIGHT.dec()
            if hb is not None:
                hb.cancel()

    def on_notify(self, *args):
        self.wakeup.set()

    async def idle(self):
        waiter = self.wakeup if WAIT == "listen" else None
        stop = asyncio.create_task(self.stopping.wait())
        pending = {stop}
        if waiter is not None:
            pending.add(asyncio.create_task(waiter.wait()))
            await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
        else:
            await asyncio.wait(pending, timeout=T_POLL)
        for t in pending:
            t.cancel()

    async def shutdown(self):
        log("SIGTERM received, no new claims")
        self.stopping.set()
        if self.current is None or self.current.done():
            return
        try:
            await asyncio.wait_for(asyncio.shield(self.current), SHUTDOWN_TIMEOUT)
        except asyncio.TimeoutError:
            task = self.current_task
            await self.on_main(lambda conn: conn.execute(
                "UPDATE tasks SET status = 'queued' WHERE id = $1 AND token = $2", task["id"], task["token"]))
            log(f"requeued on shutdown task={task['id']} token={task['token']}")
            log("exit 0")
            sys.stdout.flush()
            os._exit(0)

    async def run(self):
        start_http_server(9100)
        self.main = await self.connect("main")
        if SAFE:
            self.hb = await self.connect("hb")
        if WAIT == "listen":
            self.listener = await self.connect("listen")
            await self.listener.add_listener("tasks_new", self.on_notify)
        loop = asyncio.get_running_loop()
        stopper = []
        loop.add_signal_handler(signal.SIGTERM, lambda: stopper.append(asyncio.create_task(self.shutdown())))
        log(f"started claim={CLAIM} safe={SAFE} wait={WAIT} lease={LEASE}s poll={T_POLL * 1000:g}ms fail_rate={FAIL_RATE}")
        while not self.stopping.is_set():
            self.wakeup.clear()
            task = await self.claim()
            if task is None:
                await self.idle()
                continue
            self.current_task = task
            self.current = asyncio.create_task(self.execute(task))
            await asyncio.wait([self.current])
            if self.current.exception():
                log(f"error task={task['id']}: {self.current.exception()!r}")
        if stopper:
            await stopper[0]
        for conn in (self.main, self.hb, self.listener):
            if conn is not None:
                await conn.close()
        log("exit 0")


if __name__ == "__main__":
    asyncio.run(Worker().run())
