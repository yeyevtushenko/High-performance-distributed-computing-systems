import argparse
import asyncio
import csv
import http.client
import json
import random
import time
from urllib.parse import urlsplit

TIMEOUT = 5.0
CONNECTIONS = 20


async def post(conn, host, path):
    reader, writer = conn
    writer.write(f"POST {path} HTTP/1.1\r\nHost: {host}\r\nContent-Length: 0\r\n\r\n".encode())
    await writer.drain()
    head = await reader.readuntil(b"\r\n\r\n")
    lines = head.decode("latin-1").split("\r\n")
    status = int(lines[0].split(" ", 2)[1])
    length = 0
    for line in lines[1:]:
        name, _, value = line.partition(":")
        if name.strip().lower() == "content-length":
            length = int(value)
    body = await reader.readexactly(length) if length else b""
    return status, body


async def create_tasks(args):
    parts = urlsplit(args.url)
    host, port = parts.hostname, parts.port or 80
    ids = json.load(open(args.ids))
    rnd = random.Random(args.seed)
    count = args.count if args.count else round(args.duration * args.rate)
    free = asyncio.Queue()
    for _ in range(CONNECTIONS):
        free.put_nowait(None)
    rows = []

    async def one(i, sched):
        record = rnd.choice(ids)
        conn = await free.get()
        sent = time.time()
        try:
            if conn is None:
                conn = await asyncio.open_connection(host, port)
            status, body = await asyncio.wait_for(post(conn, host, f"/donations/{record}/tasks"), TIMEOUT)
            data = json.loads(body) if status == 202 else {}
            rows.append([i, f"{sched:.6f}", f"{sent:.6f}", status, record, data.get("id", ""), data.get("created_at", "")])
        except (OSError, asyncio.TimeoutError, asyncio.IncompleteReadError, ValueError) as e:
            if conn is not None:
                conn[1].close()
            conn = None
            rows.append([i, f"{sched:.6f}", f"{sent:.6f}", type(e).__name__, record, "", ""])
        free.put_nowait(conn)

    t0 = time.time() + 0.2
    pending = []
    for i in range(count):
        sched = t0 + i / args.rate
        delay = sched - time.time()
        if delay > 0:
            await asyncio.sleep(delay)
        pending.append(asyncio.create_task(one(i, sched)))
    await asyncio.gather(*pending)
    rows.sort(key=lambda r: r[0])
    with open(args.out, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["i", "scheduled", "sent", "status", "record_id", "task_id", "created_at"])
        w.writerows(rows)
    ok = sum(1 for r in rows if r[3] == 202)
    print(f"tasks: sent {count}, 202 {ok}, other {count - ok}, rate {args.rate}/s, duration {count / args.rate:.1f}s")


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
    t = sub.add_parser("tasks")
    t.add_argument("--url", default="http://localhost:8080")
    t.add_argument("--ids", default="results/ids.json")
    t.add_argument("--rate", type=float, required=True)
    t.add_argument("--count", type=int)
    t.add_argument("--duration", type=float)
    t.add_argument("--seed", type=int, default=0)
    t.add_argument("--out", required=True)
    args = p.parse_args()
    seed(args) if args.mode == "seed" else asyncio.run(create_tasks(args))


if __name__ == "__main__":
    main()
