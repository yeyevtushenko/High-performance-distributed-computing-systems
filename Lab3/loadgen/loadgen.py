import argparse
import asyncio
import csv
import http.client
import json
import math
import multiprocessing as mp
import os
import queue
import random
import statistics
import time
from urllib.parse import urlsplit

TIMEOUT = 5.0
FLUSH_INTERVAL = 0.5
STEP_TAIL = 20
REQUESTS_PER_CONNECTION = 100


def segments_of(profile):
    if profile["mode"] == "segments":
        begin = 0.0
        for duration, rate in profile["segments"]:
            yield begin, begin + duration, rate
            begin += duration
        return
    yield 0.0, profile["warmup"], profile["warmup_rate"]
    for k in range(1000):
        begin = profile["warmup"] + k * profile["step"]
        yield begin, begin + profile["step"], profile["start"] + k * profile["increment"]


def rate_at(profile, t):
    for begin, end, rate in segments_of(profile):
        if begin <= t < end:
            return rate
    return None


def schedule(profile, idx, procs):
    for begin, end, rate in segments_of(profile):
        n = round((end - begin) * rate)
        for j in range(idx, n, procs):
            yield begin + j / rate


def new_bucket():
    return {"sent": 0, "ok": 0, "non2xx": 0, "conn_err": 0, "lat": []}


async def exchange(conn, host, path):
    reader, writer = conn[0], conn[1]
    writer.write(f"GET {path} HTTP/1.1\r\nHost: {host}\r\n\r\n".encode())
    await writer.drain()
    head = await reader.readuntil(b"\r\n\r\n")
    lines = head.decode("latin-1").split("\r\n")
    status = int(lines[0].split(" ", 2)[1])
    length = 0
    for line in lines[1:]:
        name, _, value = line.partition(":")
        if name.strip().lower() == "content-length":
            length = int(value)
    if length:
        await reader.readexactly(length)
    return status


async def worker_main(idx, procs, url, paths, profile, connections, t0, stop, out):
    parts = urlsplit(url)
    host, port = parts.hostname, parts.port or 80
    seconds = {}
    free = asyncio.Queue()
    for _ in range(connections):
        free.put_nowait(None)
    rnd = random.Random(idx)
    tasks = set()

    def bucket(t):
        s = int(t - t0)
        b = seconds.get(s)
        if b is None:
            b = seconds[s] = new_bucket()
        return b

    async def request(path, held):
        slot = await free.get()
        held.append(slot)
        if slot is None or slot[2] >= REQUESTS_PER_CONNECTION:
            if slot is not None:
                slot[1].close()
            held[0] = None
            reader, writer = await asyncio.open_connection(host, port)
            slot = held[0] = (reader, writer, 0)
        bucket(time.time())["sent"] += 1
        try:
            status = await exchange(slot, host, path)
        except BaseException:
            slot[1].close()
            held[0] = None
            raise
        held[0] = (slot[0], slot[1], slot[2] + 1)
        return status

    async def one(sched):
        held = []
        try:
            status = await asyncio.wait_for(request(rnd.choice(paths), held), TIMEOUT)
        except asyncio.TimeoutError:
            bucket(time.time())["non2xx"] += 1
            return
        except (OSError, asyncio.IncompleteReadError, ValueError, IndexError):
            bucket(time.time())["conn_err"] += 1
            return
        finally:
            if held:
                free.put_nowait(held[0])
        end = time.time()
        b = bucket(end)
        b["ok" if 200 <= status < 300 else "non2xx"] += 1
        b["lat"].append(end - (t0 + sched))

    async def flusher():
        while True:
            await asyncio.sleep(FLUSH_INTERVAL)
            now = int(time.time() - t0)
            done = {s: b for s, b in seconds.items() if s < now}
            for s in done:
                del seconds[s]
            if done:
                out.put((idx, done))

    flush_task = asyncio.create_task(flusher())
    for sched in schedule(profile, idx, procs):
        if stop.is_set():
            break
        delay = t0 + sched - time.time()
        if delay > 0:
            await asyncio.sleep(delay)
        if stop.is_set():
            break
        t = asyncio.create_task(one(sched))
        tasks.add(t)
        t.add_done_callback(tasks.discard)
    if tasks:
        await asyncio.wait(tasks)
    flush_task.cancel()
    out.put((idx, dict(seconds)))
    out.put((idx, None))


def worker(*args):
    asyncio.run(worker_main(*args))


def percentile(values, q):
    if not values:
        return None
    values = sorted(values)
    return values[max(0, min(len(values) - 1, math.ceil(q / 100 * len(values)) - 1))]


def merge(total, part):
    for s, b in part.items():
        t = total.setdefault(s, new_bucket())
        for k in ("sent", "ok", "non2xx", "conn_err"):
            t[k] += b[k]
        t["lat"].extend(b["lat"])


def summarize_step(seconds, begin, end, target, step, t0):
    rows = [seconds.get(s, new_bucket()) for s in range(int(end - STEP_TAIL), int(end))]
    achieved = [r["ok"] for r in rows]
    lat = [x for r in rows for x in r["lat"]]
    total = sum(r["ok"] + r["non2xx"] + r["conn_err"] for r in rows)
    ms = lambda v: round(v * 1000, 2) if v is not None else None
    return {
        "step": step,
        "target_rps": target,
        "start_unix": round(t0 + begin, 3),
        "end_unix": round(t0 + end, 3),
        "tail_start_unix": round(t0 + end - STEP_TAIL, 3),
        "achieved_rps": round(statistics.mean(achieved), 2),
        "achieved_std": round(statistics.pstdev(achieved), 2),
        "sent_rps": round(statistics.mean(r["sent"] for r in rows), 2),
        "p50_ms": ms(percentile(lat, 50)),
        "p95_ms": ms(percentile(lat, 95)),
        "p99_ms": ms(percentile(lat, 99)),
        "non2xx_share": round(sum(r["non2xx"] for r in rows) / total, 5) if total else None,
        "conn_errors": sum(r["conn_err"] for r in rows),
    }


def run(args):
    if args.path:
        paths = [args.path]
    else:
        with open(args.ids) as f:
            paths = [f"/donations/{i}" for i in json.load(f)]
    if args.mode == "segments":
        segs = [tuple(float(x) for x in part.split(":")) for part in args.profile.split(",")]
        profile = {"mode": "segments", "segments": segs}
    else:
        profile = {"mode": "step", "warmup": args.warmup, "warmup_rate": args.warmup_rate, "start": args.start,
                   "increment": args.increment, "step": args.step}
    procs = args.processes
    per_proc = [args.connections // procs + (1 if i < args.connections % procs else 0) for i in range(procs)]
    ctx = mp.get_context("spawn")
    stop = ctx.Event()
    out = ctx.Queue()
    t0 = time.time() + 2.0
    workers = [ctx.Process(target=worker, args=(i, procs, args.url, paths, profile, per_proc[i], t0, stop, out))
               for i in range(procs)]
    for w in workers:
        w.start()
    seconds = {}
    steps = []
    alive = procs
    below = 0
    k = 0
    end_at = sum(d for d, _ in profile["segments"]) if profile["mode"] == "segments" else None
    while alive:
        try:
            _, part = out.get(timeout=0.2)
        except queue.Empty:
            part = {}
        else:
            if part is None:
                alive -= 1
                continue
            merge(seconds, part)
        if profile["mode"] != "step" or stop.is_set():
            continue
        begin = profile["warmup"] + k * profile["step"]
        end = begin + profile["step"]
        if time.time() - t0 < end + FLUSH_INTERVAL * 3 + 0.5:
            continue
        s = summarize_step(seconds, begin, end, profile["start"] + k * profile["increment"], k + 1, t0)
        steps.append(s)
        print(json.dumps(s), flush=True)
        below = below + 1 if s["achieved_rps"] < 0.9 * s["target_rps"] else 0
        k += 1
        if below >= 2:
            stop.set()
    for w in workers:
        w.join()
    write_results(args, seconds, steps, profile, t0, end_at)


def write_results(args, seconds, steps, profile, t0, end_at):
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    ms = lambda v: round(v * 1000, 2) if v is not None else ""
    with open(args.out + "_seconds.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["unix", "second", "target_rps", "sent", "achieved", "ok_2xx", "non_2xx", "conn_errors", "p50_ms", "p95_ms", "p99_ms"])
        for s in sorted(seconds):
            if s < 0 or (end_at is not None and s >= end_at):
                continue
            b = seconds[s]
            w.writerow([round(t0 + s, 3), s, rate_at(profile, s), b["sent"], b["ok"], b["ok"], b["non2xx"], b["conn_err"],
                        ms(percentile(b["lat"], 50)), ms(percentile(b["lat"], 95)), ms(percentile(b["lat"], 99))])
    summary = {"url": args.url, "processes": args.processes, "connections": args.connections,
               "instances": args.instances, "t0_unix": round(t0, 3), "profile": profile, "steps": steps}
    if profile["mode"] == "segments":
        segs = []
        for begin, end, rate in segments_of(profile):
            rows = [seconds.get(s, new_bucket()) for s in range(int(begin), int(end))]
            lat = [x for r in rows for x in r["lat"]]
            segs.append({"begin_s": begin, "end_s": end, "target_rps": rate,
                         "p50_ms": ms(percentile(lat, 50)), "p99_ms": ms(percentile(lat, 99)),
                         "ok_2xx": sum(r["ok"] for r in rows), "non_2xx": sum(r["non2xx"] for r in rows),
                         "conn_errors": sum(r["conn_err"] for r in rows)})
        summary["segments"] = segs
    if steps:
        best = max(steps, key=lambda s: s["achieved_rps"])
        summary.update({"X": best["achieved_rps"], "X_std": best["achieved_std"], "X_step": best["step"],
                        "X_target_rps": best["target_rps"],
                        "last_step_achieved_ratio": round(steps[-1]["achieved_rps"] / steps[-1]["target_rps"], 4)})
    with open(args.out + "_steps.json", "w") as f:
        json.dump(summary, f, indent=2)


def seed(args):
    parts = urlsplit(args.url)
    rnd = random.Random(1)
    ids = []
    for n in range(args.count):
        body = json.dumps({
            "donor_code": f"D-{n + 1:04d}",
            "blood_group": rnd.choice(["O", "A", "B", "AB"]),
            "rh_factor": rnd.choice(["+", "-"]),
            "volume_ml": rnd.randrange(200, 501, 50),
            "donation_date": f"2026-{rnd.randint(1, 9):02d}-{rnd.randint(1, 28):02d}",
            "is_suitable": rnd.random() > 0.1,
        })
        conn = http.client.HTTPConnection(parts.hostname, parts.port or 80, timeout=10)
        conn.request("POST", "/donations", body, {"Content-Type": "application/json"})
        resp = conn.getresponse()
        data = json.loads(resp.read())
        conn.close()
        if resp.status != 201:
            raise SystemExit(f"seed failed: {resp.status} {data}")
        ids.append(data["id"])
    with open(args.ids, "w") as f:
        json.dump(ids, f)
    print(f"seeded {len(ids)} records")


def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="mode", required=True)
    s = sub.add_parser("seed")
    s.add_argument("--url", default="http://localhost:8080")
    s.add_argument("--count", type=int, default=100)
    s.add_argument("--ids", default="results/ids.json")
    for name in ("step", "segments"):
        r = sub.add_parser(name)
        r.add_argument("--url", default="http://localhost:8080")
        r.add_argument("--ids", default="results/ids.json")
        r.add_argument("--path")
        r.add_argument("--processes", type=int, default=8)
        r.add_argument("--connections", type=int, default=40)
        r.add_argument("--instances", type=int, default=1)
        r.add_argument("--out", required=True)
        if name == "step":
            r.add_argument("--warmup", type=float, default=10)
            r.add_argument("--warmup-rate", type=float, default=200)
            r.add_argument("--start", type=float, default=200)
            r.add_argument("--increment", type=float, default=200)
            r.add_argument("--step", type=float, default=30)
        else:
            r.add_argument("--profile", required=True)
    args = p.parse_args()
    seed(args) if args.mode == "seed" else run(args)


if __name__ == "__main__":
    main()
