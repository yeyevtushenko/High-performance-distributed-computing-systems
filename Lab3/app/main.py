import asyncio
import os
import socket
import time
from contextlib import asynccontextmanager
from datetime import date
from typing import Literal

import asyncpg
from fastapi import FastAPI, Path, Query, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, REGISTRY, Counter, Gauge, Histogram, generate_latest
from prometheus_client.core import CounterMetricFamily
from pydantic import BaseModel, ConfigDict, Field

DEFAULT_LIMIT = 20
MAX_LIMIT = 100
COLUMNS = "id, donor_code, blood_group, rh_factor, volume_ml, donation_date, is_suitable"
POD_NAME = os.environ.get("POD_NAME") or socket.gethostname()
DB_POOL_SIZE = int(os.environ.get("DB_POOL_SIZE", "10"))
BUCKETS = (0.002, 0.005, 0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1.0, 2.0)
CGROUP_CPU_STAT = ("/sys/fs/cgroup/cpu.stat", "/sys/fs/cgroup/cpu,cpuacct/cpu.stat")

REQUESTS = Counter("http_requests", "HTTP requests", ["method", "path", "code"])
DURATION = Histogram("http_request_duration_seconds", "Request processing time", buckets=BUCKETS)
IN_PROGRESS = Gauge("http_requests_in_progress", "Requests being processed")

BloodGroup = Literal["O", "A", "B", "AB"]


class CgroupCpuCollector:
    def collect(self):
        for path in CGROUP_CPU_STAT:
            try:
                with open(path) as f:
                    stats = dict(line.split() for line in f if line.strip())
            except OSError:
                continue
            yield CounterMetricFamily("cgroup_cpu_nr_periods", "CFS periods", value=int(stats["nr_periods"]))
            yield CounterMetricFamily("cgroup_cpu_nr_throttled", "Throttled CFS periods", value=int(stats["nr_throttled"]))
            return


REGISTRY.register(CgroupCpuCollector())


class DonationIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    donor_code: str = Field(min_length=1, max_length=32, pattern=r"^[^\x00-\x1f\x7f]+$")
    blood_group: BloodGroup
    rh_factor: Literal["+", "-"]
    volume_ml: int = Field(ge=200, le=500)
    donation_date: date
    is_suitable: bool


state = {"pool": None}


async def connect():
    while state["pool"] is None:
        try:
            state["pool"] = await asyncpg.create_pool(min_size=min(2, DB_POOL_SIZE), max_size=DB_POOL_SIZE)
        except (OSError, asyncpg.PostgresError):
            await asyncio.sleep(1)


@asynccontextmanager
async def lifespan(app):
    task = asyncio.create_task(connect())
    yield
    task.cancel()
    if state["pool"] is not None:
        await state["pool"].close()


class MetricsMiddleware:
    def __init__(self, app):
        self.app = app
        self.pod_header = (b"x-pod", POD_NAME.encode())

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        status = [500]

        async def send_with_pod(message):
            if message["type"] == "http.response.start":
                status[0] = message["status"]
                message["headers"] = [*message.get("headers", []), self.pod_header]
            await send(message)

        track = scope["path"] != "/metrics"
        if track:
            IN_PROGRESS.inc()
        start = time.perf_counter()
        try:
            await self.app(scope, receive, send_with_pod)
        finally:
            if track:
                DURATION.observe(time.perf_counter() - start)
                IN_PROGRESS.dec()
            route = scope.get("route")
            REQUESTS.labels(scope["method"], route.path if route else "unmatched", str(status[0])).inc()


app = FastAPI(lifespan=lifespan)
app.add_middleware(MetricsMiddleware)


@app.exception_handler(RequestValidationError)
async def validation_error(request, exc):
    errors = [
        {
            "field": ".".join(str(p) for p in e["loc"][1:]) if e["type"] != "json_invalid" and len(e["loc"]) > 1 else None,
            "message": e["msg"],
        }
        for e in exc.errors()
    ]
    return JSONResponse(status_code=400, content={"errors": errors})


def unavailable():
    return JSONResponse(status_code=503, content={"error": "database unavailable"})


def not_found():
    return JSONResponse(status_code=404, content={"error": "donation not found"})


def to_dict(row):
    d = dict(row)
    d["donation_date"] = d["donation_date"].isoformat()
    return d


RecordId = Path(ge=1, le=2**63 - 1)


@app.post("/donations", status_code=201)
async def create_donation(body: DonationIn):
    pool = state["pool"]
    if pool is None:
        return unavailable()
    row = await pool.fetchrow(
        "INSERT INTO donations (donor_code, blood_group, rh_factor, volume_ml, donation_date, is_suitable) "
        f"VALUES ($1, $2, $3, $4, $5, $6) RETURNING {COLUMNS}",
        *body.model_dump().values(),
    )
    return to_dict(row)


@app.get("/donations")
async def list_donations(
    limit: int = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    offset: int = Query(0, ge=0, le=2**31 - 1),
    blood_group: BloodGroup | None = None,
):
    pool = state["pool"]
    if pool is None:
        return unavailable()
    where, args = ("WHERE blood_group = $1", [blood_group]) if blood_group else ("", [])
    n = len(args)
    async with pool.acquire() as conn:
        total = await conn.fetchval(f"SELECT count(*) FROM donations {where}", *args)
        rows = await conn.fetch(
            f"SELECT {COLUMNS} FROM donations {where} ORDER BY id LIMIT ${n + 1} OFFSET ${n + 2}",
            *args, limit, offset,
        )
    return {"items": [to_dict(r) for r in rows], "total": total, "limit": limit, "offset": offset}


@app.get("/donations/{donation_id}")
async def get_donation(donation_id: int = RecordId):
    pool = state["pool"]
    if pool is None:
        return unavailable()
    row = await pool.fetchrow(f"SELECT {COLUMNS} FROM donations WHERE id = $1", donation_id)
    return to_dict(row) if row else not_found()


@app.put("/donations/{donation_id}")
async def replace_donation(body: DonationIn, donation_id: int = RecordId):
    pool = state["pool"]
    if pool is None:
        return unavailable()
    row = await pool.fetchrow(
        "UPDATE donations SET donor_code = $2, blood_group = $3, rh_factor = $4, volume_ml = $5, "
        f"donation_date = $6, is_suitable = $7 WHERE id = $1 RETURNING {COLUMNS}",
        donation_id, *body.model_dump().values(),
    )
    return to_dict(row) if row else not_found()


@app.delete("/donations/{donation_id}", status_code=204)
async def delete_donation(donation_id: int = RecordId):
    pool = state["pool"]
    if pool is None:
        return unavailable()
    status = await pool.execute("DELETE FROM donations WHERE id = $1", donation_id)
    return Response(status_code=204) if status == "DELETE 1" else not_found()


@app.get("/healthz")
async def healthz():
    pool = state["pool"]
    if pool is None:
        return unavailable()
    try:
        await asyncio.wait_for(pool.fetchval("SELECT 1"), timeout=2)
    except (OSError, asyncio.TimeoutError, asyncpg.PostgresError, asyncpg.InterfaceError):
        return unavailable()
    return {"status": "ok"}


@app.get("/livez")
async def livez():
    return {"status": "ok"}


@app.get("/metrics")
async def metrics():
    return Response(generate_latest(REGISTRY), media_type=CONTENT_TYPE_LATEST)
