import argparse
import json
import math
import os
import platform
import random
import statistics
import sys
import threading
import time
from array import array

from bst import BST, GlobalLock, RWLock

KEYS = 100_000
RANGE_WIDTH = 100
LOCKS = {"global": GlobalLock, "rw": RWLock}


def build(seed=0):
    tree = BST()
    keys = random.Random(seed).sample(range(KEYS), KEYS // 2)
    for k in keys:
        tree.insert(k)
    return tree


def percentile(sorted_values, q):
    if not sorted_values:
        return None
    return sorted_values[max(0, math.ceil(q * len(sorted_values)) - 1)]


def make_ops(tree, lock, role, rnd):
    if role == "w":
        ops = [(tree.insert, lock.acquire_write, lock.release_write), (tree.remove, lock.acquire_write, lock.release_write)]

        def args():
            return (rnd.randrange(KEYS),)
    else:
        ops = [(tree.contains, lock.acquire_read, lock.release_read), (tree.range_count, lock.acquire_read, lock.release_read)]

        def args():
            lo = rnd.randrange(KEYS - RANGE_WIDTH + 1)
            return (lo,) if turn[0] == 0 else (lo, lo + RANGE_WIDTH - 1)
    turn = [0]
    return ops, args, turn


def worker(tid, role, tree, lock, state, out):
    rnd = random.Random(1000 + tid)
    ops, args, turn = make_ops(tree, lock, role, rnd)
    durations = array("d")
    count = 0
    wait_sum = 0.0
    max_wait = 0.0
    perf = time.perf_counter
    state["barrier"].wait()
    m_start, m_end = state["m_start"], state["m_end"]
    while not state["stop"]:
        fn, acquire, release = ops[turn[0]]
        a = args()
        turn[0] ^= 1
        t_call = perf()
        acquire()
        t_acq = perf()
        fn(*a)
        release()
        t_ret = perf()
        if t_call < m_end and t_ret >= m_start:
            wait_sum += max(0.0, min(t_acq, m_end) - max(t_call, m_start))
            max_wait = max(max_wait, t_acq - t_call)
        if m_start <= t_ret < m_end:
            count += 1
            durations.append(t_ret - t_call)
    out[tid] = {"role": role, "count": count, "durations": durations, "wait_sum": wait_sum, "max_wait": max_wait}


def role_stats(rows, D):
    durations = sorted(x for r in rows for x in r["durations"])
    counts = [r["count"] for r in rows]
    n = sum(counts)
    mu = statistics.mean(counts)
    sigma = math.sqrt(sum((c - mu) ** 2 for c in counts) / len(counts))
    p50, p99 = percentile(durations, 0.5), percentile(durations, 0.99)
    return {"ops": n, "X": n / D, "x_per_thread": n / D / len(rows),
            "p50_us": p50 * 1e6 if n else None, "p99_us": p99 * 1e6 if n else None,
            "h": p99 / p50 if n else None, "max_wait_ms": max(r["max_wait"] for r in rows) * 1e3,
            "w": sum(r["wait_sum"] for r in rows) / (len(rows) * D), "cv": sigma / mu if mu else None}


def run(args):
    tree = build()
    lock = LOCKS[args.sync]()
    n = args.threads
    roles = {tid: ("w" if tid % 2 == 1 else "r") for tid in range(1, n + 1)}
    out = {}
    state = {"stop": False, "barrier": threading.Barrier(n + 1)}
    threads = [threading.Thread(target=worker, args=(tid, roles[tid], tree, lock, state, out)) for tid in roles]
    for t in threads:
        t.start()
    now = time.perf_counter()
    state["m_start"] = now + args.warmup
    state["m_end"] = now + args.warmup + args.duration
    state["barrier"].wait()
    time.sleep(args.warmup + args.duration + 0.05)
    state["stop"] = True
    for t in threads:
        t.join()
    writers = [out[t] for t in roles if roles[t] == "w"]
    readers = [out[t] for t in roles if roles[t] == "r"]
    w, r = role_stats(writers, args.duration), role_stats(readers, args.duration)
    total = w["X"] + r["X"]
    result = {
        "sync": args.sync, "threads": n, "n_w": len(writers), "n_r": len(readers), "duration_s": args.duration,
        "per_thread": {tid: {"role": "запис" if roles[tid] == "w" else "читання", "ops": out[tid]["count"]} for tid in roles},
        "write": w, "read": r, "X": total, "phi_w": w["X"] / total, "phi_r": r["X"] / total,
        "invariants": tree.check_invariants(),
    }
    save(args.out, result)


def single(tree, lock, plan, warmup, duration):
    rnd = random.Random(7)
    w_ops, w_args, _ = make_ops(tree, lock, "w", rnd)
    r_ops, r_args, r_turn = make_ops(tree, lock, "r", rnd)
    w_turn = [0]
    perf = time.perf_counter
    start = perf()
    m_start, m_end = start + warmup, start + warmup + duration
    count = 0
    cs = {"w": [], "r": []}
    i = 0
    while True:
        t_call = perf()
        if t_call >= m_end:
            break
        role = plan[i % len(plan)]
        i += 1
        if role == "w":
            fn, acquire, release = w_ops[w_turn[0]]
            a = w_args()
            w_turn[0] ^= 1
        else:
            fn, acquire, release = r_ops[r_turn[0]]
            a = r_args()
            r_turn[0] ^= 1
        acquire()
        t_acq = perf()
        fn(*a)
        t_rel = perf()
        release()
        if t_call >= m_start:
            count += 1
            cs[role].append(t_rel - t_acq)
    return count / duration, cs


def x1(args):
    g = math.gcd(args.n_w, args.n_r)
    plan = ["w"] * (args.n_w // g) + ["r"] * (args.n_r // g)
    x, _ = single(build(), LOCKS[args.sync](), plan, args.warmup, args.duration)
    save(args.out, {"sync": args.sync, "plan": f"{args.n_w}:{args.n_r}", "X_1": x, "duration_s": args.duration})


def tcs(args):
    _, cs_w = single(build(), GlobalLock(), ["w"], args.warmup, args.duration)
    _, cs_r = single(build(), GlobalLock(), ["r"], args.warmup, args.duration)
    t_w = statistics.mean(cs_w["w"])
    t_r = statistics.mean(cs_r["r"])
    n, n_w, n_r = args.n_w + args.n_r, args.n_w, args.n_r
    t_bar = (n_w * t_w + n_r * t_r) / n
    save(args.out, {"t_cs_w_us": t_w * 1e6, "t_cs_r_us": t_r * 1e6, "t_cs_bar_us": t_bar * 1e6, "limit_ops": 1 / t_bar})


def readonly(args):
    tree = build()
    before = tree.snapshot()
    lock = RWLock()
    out = {}
    n = args.threads
    state = {"stop": False, "barrier": threading.Barrier(n + 1)}
    now = time.perf_counter()
    state["m_start"], state["m_end"] = now, now + args.duration
    threads = [threading.Thread(target=worker, args=(tid, "r", tree, lock, state, out)) for tid in range(1, n + 1)]
    for t in threads:
        t.start()
    state["barrier"].wait()
    time.sleep(args.duration)
    state["stop"] = True
    for t in threads:
        t.join()
    after = tree.snapshot()
    save(args.out, {"reader_threads": n, "read_ops": sum(o["count"] for o in out.values()),
                    "snapshot_before": before, "snapshot_after": after, "unchanged": before == after})


def info(args):
    save(args.out, {"python": sys.version.split()[0], "implementation": platform.python_implementation(),
                    "logical_cores": os.cpu_count(), "platform": platform.platform()})


def save(path, data):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print(json.dumps({k: v for k, v in data.items() if k != "per_thread"}, ensure_ascii=False))


def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("run", "x1", "tcs", "readonly", "info"):
        s = sub.add_parser(name)
        s.add_argument("--out", required=True)
        s.add_argument("--sync", choices=LOCKS, default="global")
        s.add_argument("--threads", type=int, default=12)
        s.add_argument("--n-w", type=int, default=6)
        s.add_argument("--n-r", type=int, default=6)
        s.add_argument("--warmup", type=float, default=1)
        s.add_argument("--duration", type=float, default=10)
    args = p.parse_args()
    {"run": run, "x1": x1, "tcs": tcs, "readonly": readonly, "info": info}[args.cmd](args)


if __name__ == "__main__":
    main()
