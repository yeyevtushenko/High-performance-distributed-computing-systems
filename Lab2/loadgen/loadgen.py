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
STEP_TAIL = 15


def step_rate(profile, t):
    if t < 0:
        return None
    if t < profile["warmup"]:
        return profile["warmup_rate"]
    k = int((t - profile["warmup"]) // profile["step"])
    return profile["start"] + k * profile["increment"]


def schedule(profile, idx, procs):
    segments = []
    if profile["mode"] == "const":
        segments.append((0.0, profile["duration"], profile["rate"]))
    else:
        segments.append((0.0, profile["warmup"], profile["warmup_rate"]))
        k = 0
        while True:
            begin = profile["warmup"] + k * profile["step"]
            segments.append((begin, begin + profile["step"], profile["start"] + k * profile["increment"]))
            k += 1
            if k > 1000:
                break
    for begin, end, rate in segments:
        n = round((end - begin) * rate)
        for j in range(idx, n, procs):
            yield begin + j / rate


class Stats:
    def __init__(self):
        self.seconds = {}

    def bucket(self, s):
        b = self.seconds.get(s)
        if b is None:
            b = self.seconds[s] = {"sent": 0, "ok": 0, "non2xx": 0, "conn_err": 0, "lat": []}
        return b


async def http_get(pool, host, port, path):
    conn = pool.pop() if pool else None
    if conn is None:
        conn = await asyncio.open_connection(host, port)
    reader, writer = conn
    try:
        writer.write(f"GET {path} HTTP/1.1\r\nHost: {host}\r\n\r\n".encode())
        await writer.drain()
        head = await reader.readuntil(b"\r\n\r\n")
        lines = head.decode("latin-1").split("\r\n")
        status = int(lines[0].split(" ", 2)[1])
        length = 0
        close = False
        for line in lines[1:]:
            name, _, value = line.partition(":")
            name = name.strip().lower()
            if name == "content-length":
                length = int(value)
            elif name == "connection" and value.strip().lower() == "close":
                close = True
        if length:
            await reader.readexactly(length)
    except BaseException:
        writer.close()
        raise
    if close:
        writer.close()
    else:
        pool.append(conn)
    return status


async def worker_main(idx, procs, url, paths, profile, t0, stop, out):
    parts = urlsplit(url)
    host, port = parts.hostname, parts.port or 80
    stats = Stats()
    pool = []
    rnd = random.Random(idx)
    tasks = set()

    async def one(sched):
        now = time.time()
        stats.bucket(int(now - t0))["sent"] += 1
        try:
            status = await asyncio.wait_for(http_get(pool, host, port, rnd.choice(paths)), TIMEOUT)
        except asyncio.TimeoutError:
            stats.bucket(int(time.time() - t0))["non2xx"] += 1
            return
        except (OSError, asyncio.IncompleteReadError, ValueError, IndexError):
            stats.bucket(int(time.time() - t0))["conn_err"] += 1
            return
        end = time.time()
        b = stats.bucket(int(end - t0))
        if 200 <= status < 300:
            b["ok"] += 1
        else:
            b["non2xx"] += 1
        b["lat"].append(end - (t0 + sched))

    async def flusher():
        while True:
            await asyncio.sleep(FLUSH_INTERVAL)
            done = {s: b for s, b in stats.seconds.items() if s < int(time.time() - t0)}
            for s in done:
                del stats.seconds[s]
            if done:
                out.put((idx, done))

    flush_task = asyncio.create_task(flusher())
    end_at = profile["duration"] if profile["mode"] == "const" else None
    for sched in schedule(profile, idx, procs):
        if stop.is_set() or (end_at is not None and sched >= end_at):
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
    out.put((idx, dict(stats.seconds)))
    out.put((idx, None))


def worker(idx, procs, url, paths, profile, t0, stop, out):
    asyncio.run(worker_main(idx, procs, url, paths, profile, t0, stop, out))


def percentile(values, q):
    if not values:
        return None
    values = sorted(values)
    k = max(0, min(len(values) - 1, math.ceil(q / 100 * len(values)) - 1))
    return values[k]


def merge(total, part):
    for s, b in part.items():
        t = total.setdefault(s, {"sent": 0, "ok": 0, "non2xx": 0, "conn_err": 0, "lat": []})
        for k in ("sent", "ok", "non2xx", "conn_err"):
            t[k] += b[k]
        t["lat"].extend(b["lat"])


def summarize_step(seconds, profile, k, t0):
    begin = profile["warmup"] + k * profile["step"]
    end = begin + profile["step"]
    tail = [s for s in range(int(end - STEP_TAIL), int(end))]
    rows = [seconds.get(s, {"sent": 0, "ok": 0, "non2xx": 0, "conn_err": 0, "lat": []}) for s in tail]
    achieved = [r["ok"] for r in rows]
    lat = [x for r in rows for x in r["lat"]]
    total = sum(r["ok"] + r["non2xx"] + r["conn_err"] for r in rows)
    ms = lambda v: round(v * 1000, 2) if v is not None else None
    return {
        "step": k + 1,
        "target_rps": profile["start"] + k * profile["increment"],
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
    if args.mode == "const":
        profile = {"mode": "const", "rate": args.rate, "duration": args.duration}
    else:
        profile = {"mode": "step", "warmup": args.warmup, "warmup_rate": args.warmup_rate, "start": args.start,
                   "increment": args.increment, "step": args.step}
    procs = args.processes
    ctx = mp.get_context("spawn")
    stop = ctx.Event()
    out = ctx.Queue()
    t0 = time.time() + 2.0
    workers = [ctx.Process(target=worker, args=(i, procs, args.url, paths, profile, t0, stop, out)) for i in range(procs)]
    for w in workers:
        w.start()
    seconds = {}
    steps = []
    alive = procs
    below = 0
    next_step = 0
    while alive:
        try:
            idx, part = out.get(timeout=0.2)
        except queue.Empty:
            part = {}
        else:
            if part is None:
                alive -= 1
                continue
            merge(seconds, part)
        if profile["mode"] != "step" or stop.is_set():
            continue
        step_end = profile["warmup"] + (next_step + 1) * profile["step"]
        if time.time() - t0 < step_end + FLUSH_INTERVAL * 3 + 0.5:
            continue
        s = summarize_step(seconds, profile, next_step, t0)
        steps.append(s)
        print(json.dumps(s), flush=True)
        below = below + 1 if s["achieved_rps"] < 0.9 * s["target_rps"] else 0
        next_step += 1
        if below >= 2 or (args.limit and s["target_rps"] >= args.limit):
            stop.set()
    if profile["mode"] == "const":
        stop.set()
    for w in workers:
        w.join()
    write_results(args, seconds, steps, profile, t0)


def write_results(args, seconds, steps, profile, t0):
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    ms = lambda v: round(v * 1000, 2) if v is not None else ""
    with open(args.out + "_seconds.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["unix", "second", "target_rps", "sent", "achieved", "ok_2xx", "non_2xx", "conn_errors", "p50_ms", "p95_ms", "p99_ms"])
        for s in sorted(seconds):
            if s < 0:
                continue
            b = seconds[s]
            target = profile["rate"] if profile["mode"] == "const" else step_rate(profile, s)
            w.writerow([round(t0 + s, 3), s, target, b["sent"], b["ok"], b["ok"], b["non2xx"], b["conn_err"],
                        ms(percentile(b["lat"], 50)), ms(percentile(b["lat"], 95)), ms(percentile(b["lat"], 99))])
    summary = {"url": args.url, "path": args.path or "/donations/{id}", "processes": args.processes,
               "instances": args.instances, "t0_unix": round(t0, 3), "profile": profile, "steps": steps}
    if steps:
        best = max(steps, key=lambda s: s["achieved_rps"])
        summary.update({"X": best["achieved_rps"], "X_std": best["achieved_std"], "X_step": best["step"],
                        "X_target_rps": best["target_rps"], "last_step_achieved_ratio":
                        round(steps[-1]["achieved_rps"] / steps[-1]["target_rps"], 4)})
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
    for name in ("step", "const"):
        r = sub.add_parser(name)
        r.add_argument("--url", default="http://localhost:8080")
        r.add_argument("--ids", default="results/ids.json")
        r.add_argument("--path")
        r.add_argument("--processes", type=int, default=os.cpu_count())
        r.add_argument("--instances", type=int, default=1)
        r.add_argument("--out", required=True)
        if name == "step":
            r.add_argument("--warmup", type=float, default=10)
            r.add_argument("--warmup-rate", type=float, default=200)
            r.add_argument("--start", type=float, default=200)
            r.add_argument("--increment", type=float, default=200)
            r.add_argument("--step", type=float, default=20)
            r.add_argument("--limit", type=float)
        else:
            r.add_argument("--rate", type=float, required=True)
            r.add_argument("--duration", type=float, required=True)
    args = p.parse_args()
    seed(args) if args.mode == "seed" else run(args)


if __name__ == "__main__":
    main()
