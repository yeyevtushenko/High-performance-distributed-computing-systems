import argparse
import bisect
import csv
import gzip
import http.client
import json
import multiprocessing as mp
import os
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlsplit

TIMEOUT = 10
SAMPLE_INTERVAL = 0.01
THREADS_PER_PROCESS = 256


class Client:
    def __init__(self, host, port):
        self.host, self.port = host, port
        self.local = threading.local()

    def get(self, path):
        conn = getattr(self.local, "conn", None)
        if conn is None:
            conn = self.local.conn = http.client.HTTPConnection(self.host, self.port, timeout=TIMEOUT)
        try:
            conn.request("GET", path)
            resp = conn.getresponse()
            resp.read()
            return resp.status
        except Exception:
            conn.close()
            self.local.conn = None
            return 0


def open_worker(args):
    idx, procs, url, paths, rate, warmup, duration, start = args
    parts = urlsplit(url)
    client = Client(parts.hostname, parts.port or 80)
    total = round((warmup + duration) * rate)
    schedule = [start + j / rate for j in range(idx, total, procs)]
    m_start, m_end = start + warmup, start + warmup + duration
    records = []
    sent, completed = [0], [0]
    lock = threading.Lock()
    samples = []
    stop = threading.Event()

    def sampler():
        while not stop.is_set():
            now = time.monotonic()
            if m_start <= now < m_end:
                with lock:
                    samples.append((bisect.bisect_right(schedule, now) - completed[0], sent[0] - completed[0]))
            time.sleep(SAMPLE_INTERVAL)

    def request(sched, path):
        with lock:
            sent[0] += 1
        send = time.monotonic()
        status = client.get(path)
        end = time.monotonic()
        with lock:
            completed[0] += 1
        records.append((sched, send, end, status))

    threading.Thread(target=sampler, daemon=True).start()
    rnd = random.Random(idx)
    with ThreadPoolExecutor(THREADS_PER_PROCESS) as pool:
        for sched in schedule:
            delay = sched - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            pool.submit(request, sched, rnd.choice(paths))
    stop.set()
    count = len(samples) or 1
    return records, (sum(s[0] for s in samples) / count, sum(s[1] for s in samples) / count)


def closed_worker(args):
    idx, url, paths, threads, warmup, duration, start = args
    parts = urlsplit(url)
    client = Client(parts.hostname, parts.port or 80)
    end_at = start + warmup + duration
    records = []

    def loop(seed):
        rnd = random.Random(seed)
        while True:
            send = time.monotonic()
            if send >= end_at:
                return
            status = client.get(rnd.choice(paths))
            records.append((send, send, time.monotonic(), status))

    delay = start - time.monotonic()
    if delay > 0:
        time.sleep(delay)
    with ThreadPoolExecutor(threads) as pool:
        for t in range(threads):
            pool.submit(loop, idx * 1000 + t)
    return records, (0.0, 0.0)


def percentile(sorted_values, q):
    if not sorted_values:
        return None
    k = max(0, min(len(sorted_values) - 1, round(q / 100 * len(sorted_values) + 0.5) - 1))
    return sorted_values[k]


def summarize(records, in_flight, start, warmup, duration):
    m_start, m_end = start + warmup, start + warmup + duration
    window = [r for r in records if m_start <= r[0] < m_end]
    latencies = sorted(r[2] - r[0] for r in window)
    completed = sum(1 for r in records if m_start <= r[2] < m_end)
    non_2xx = sum(1 for r in window if not 200 <= r[3] < 300)
    ms = lambda v: round(v * 1000, 2) if v is not None else None
    return {
        "requests": len(window),
        "achieved_rps": round(completed / duration, 2),
        "p50_ms": ms(percentile(latencies, 50)),
        "p95_ms": ms(percentile(latencies, 95)),
        "p99_ms": ms(percentile(latencies, 99)),
        "mean_ms": ms(sum(latencies) / len(latencies) if latencies else None),
        "non_2xx_share": round(non_2xx / len(window), 4) if window else None,
        "mean_in_flight": round(in_flight[0], 4),
        "mean_sent_in_flight": round(in_flight[1], 4),
    }


def write_raw(path, records, start):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with gzip.open(path, "wt", newline="") as f:
        w = csv.writer(f)
        w.writerow(["scheduled_s", "sent_s", "finished_s", "status"])
        for r in sorted(records):
            w.writerow([f"{r[0] - start:.6f}", f"{r[1] - start:.6f}", f"{r[2] - start:.6f}", r[3]])


def run(args):
    if args.path:
        paths = [args.path]
    else:
        with open(args.ids) as f:
            paths = [f"/donations/{i}" for i in json.load(f)]
    procs = args.processes
    start = time.monotonic() + 1.0
    ctx = mp.get_context("fork")
    with ctx.Pool(procs) as pool:
        if args.mode == "open":
            jobs = [(i, procs, args.url, paths, args.rate, args.warmup, args.duration, start) for i in range(procs)]
            results = pool.map(open_worker, jobs)
        else:
            per = [args.concurrency // procs + (1 if i < args.concurrency % procs else 0) for i in range(procs)]
            jobs = [(i, args.url, paths, n, args.warmup, args.duration, start) for i, n in enumerate(per) if n]
            results = pool.map(closed_worker, jobs)
    records = [r for recs, _ in results for r in recs]
    in_flight = tuple(sum(l[k] for _, l in results) for k in (0, 1))
    summary = summarize(records, in_flight, start, args.warmup, args.duration)
    summary.update({
        "mode": args.mode,
        "target_rps": args.rate if args.mode == "open" else None,
        "concurrency": args.concurrency if args.mode == "closed" else None,
        "instances": args.instances,
        "path": args.path or "/donations/{id}",
        "processes": procs,
        "warmup_s": args.warmup,
        "duration_s": args.duration,
    })
    if args.out:
        os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
        with open(args.out, "w") as f:
            json.dump(summary, f, indent=2)
        write_raw(args.out.replace("results/", "results/raw/").replace(".json", ".csv.gz"), records, start)
    print(json.dumps(summary))


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
        conn = http.client.HTTPConnection(parts.hostname, parts.port or 80, timeout=TIMEOUT)
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
    for name in ("open", "closed"):
        r = sub.add_parser(name)
        r.add_argument("--url", default="http://localhost:8080")
        r.add_argument("--ids", default="results/ids.json")
        r.add_argument("--path")
        r.add_argument("--warmup", type=float, default=10)
        r.add_argument("--duration", type=float, default=30)
        r.add_argument("--processes", type=int, default=os.cpu_count())
        r.add_argument("--instances", type=int, default=1)
        r.add_argument("--out")
        if name == "open":
            r.add_argument("--rate", type=float, required=True)
        else:
            r.add_argument("--concurrency", type=int, required=True)
    args = p.parse_args()
    seed(args) if args.mode == "seed" else run(args)


if __name__ == "__main__":
    main()
