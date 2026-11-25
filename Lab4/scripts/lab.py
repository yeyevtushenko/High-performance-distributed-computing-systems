import argparse
import json
import math
import os
import re
import statistics
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESULTS = os.path.join(ROOT, "results")
PROM = "http://localhost:9090"
R = 5
T_POLL_MS = 50
LEASE_S = 5
FINAL = {"CLAIM": "skip_locked", "SAFE": "1", "WAIT": "poll", "T_POLL_MS": str(T_POLL_MS), "FAIL_RATE": "0"}


def sh(*cmd, input=None, check=True):
    p = subprocess.run(cmd, input=input, capture_output=True, text=True)
    if check and p.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd)}: {p.stderr}")
    return p.stdout


def sql(query):
    return sh("kubectl", "exec", "-i", "db-0", "--", "sh", "-c",
              'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -At -v ON_ERROR_STOP=1', input=query).strip()


def sql_json(query):
    out = sql(f"SELECT coalesce(json_agg(t), '[]') FROM ({query}) t")
    return json.loads(out)


def sql_one(query):
    rows = sql_json(query)
    return rows[0] if rows else None


def prom(query, at=None):
    params = {"query": query}
    if at:
        params["time"] = at
    with urllib.request.urlopen(f"{PROM}/api/v1/query?{urllib.parse.urlencode(params)}") as r:
        return json.load(r)["data"]["result"]


def prom_range(query, start, end, step=5):
    params = urllib.parse.urlencode({"query": query, "start": start, "end": end, "step": step})
    with urllib.request.urlopen(f"{PROM}/api/v1/query_range?{params}") as r:
        return json.load(r)["data"]["result"]


def save(name, data):
    path = os.path.join(RESULTS, name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False, default=str)
    print(f"[{name}] {json.dumps(data, ensure_ascii=False, default=str)[:1500]}", flush=True)


def note(msg):
    print(f"{datetime.now().strftime('%H:%M:%S')} {msg}", flush=True)


def worker_pods():
    out = sh("kubectl", "get", "pods", "-l", "app=worker", "-o", "json")
    return json.loads(out)["items"]


def set_workers(replicas, **env):
    if env:
        sh("kubectl", "set", "env", "deployment/worker", *[f"{k}={v}" for k, v in env.items()])
    sh("kubectl", "scale", "deployment/worker", f"--replicas={replicas}")
    sh("kubectl", "rollout", "status", "deployment/worker", "--timeout=300s")
    while True:
        pods = worker_pods()
        ready = [p for p in pods if p["metadata"].get("deletionTimestamp") is None and all(
            c.get("ready") for c in p["status"].get("containerStatuses", []) or [{}])]
        if len(pods) == replicas and len(ready) == replicas:
            break
        time.sleep(1)
    time.sleep(2)
    return [p["metadata"]["name"] for p in pods]


def reset():
    sql("TRUNCATE task_results, tasks RESTART IDENTITY")


def wait_empty(timeout=900):
    start = time.time()
    while time.time() - start < timeout:
        left = int(sql("SELECT count(*) FROM tasks WHERE status IN ('queued', 'running')"))
        if left == 0:
            return True
        time.sleep(1)
    return False


def generate(out, rate, count=None, duration=None, seed=0):
    os.makedirs(os.path.dirname(os.path.join(RESULTS, out)), exist_ok=True)
    cmd = [sys.executable, os.path.join(ROOT, "loadgen", "loadgen.py"), "tasks", "--rate", str(rate),
           "--ids", os.path.join(RESULTS, "ids.json"), "--out", os.path.join(RESULTS, out), "--seed", str(seed)]
    cmd += ["--count", str(count)] if count else ["--duration", str(duration)]
    return subprocess.Popen(cmd)


class Logs:
    def __init__(self, folder):
        self.folder = os.path.join(RESULTS, folder, "logs")
        os.makedirs(self.folder, exist_ok=True)
        self.since = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.seen = set()
        self.current = {}
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.loop, daemon=True)
        self.thread.start()

    def loop(self):
        while not self.stop.is_set():
            try:
                pods = worker_pods()
            except RuntimeError:
                pods = []
            for p in pods:
                name = p["metadata"]["name"]
                for c in p["status"].get("containerStatuses", []) or []:
                    key = (name, c.get("restartCount", 0))
                    if key in self.seen or "running" not in c.get("state", {}):
                        continue
                    old = self.current.get(name)
                    if old is not None and old.poll() is None:
                        old.terminate()
                        old.wait()
                    self.seen.add(key)
                    f = open(os.path.join(self.folder, f"{name}.r{key[1]}.log"), "w")
                    self.current[name] = subprocess.Popen(
                        ["kubectl", "logs", "-f", f"--since-time={self.since}", name],
                        stdout=f, stderr=subprocess.DEVNULL)
                    f.close()
            time.sleep(1)

    def close(self):
        time.sleep(2)
        self.stop.set()
        self.thread.join()
        for p in self.current.values():
            if p.poll() is None:
                p.terminate()
            p.wait()

    def lines(self, pattern=""):
        out = []
        for name in sorted(os.listdir(self.folder)):
            with open(os.path.join(self.folder, name)) as f:
                out += [line.rstrip("\n") for line in f if pattern in line]
        return out


def scrape(pod):
    code = "import urllib.request;print(urllib.request.urlopen('http://localhost:9100/metrics').read().decode())"
    text = sh("kubectl", "exec", pod, "--", "python", "-c", code)
    data = {}
    for line in text.splitlines():
        if line.startswith("#") or not line.strip():
            continue
        name, value = line.rsplit(" ", 1)
        data[name] = float(value)
    return data


def scrape_all(pods):
    with ThreadPoolExecutor(len(pods)) as ex:
        parts = list(ex.map(scrape, pods))
    total = {}
    for part in parts:
        for k, v in part.items():
            total[k] = total.get(k, 0.0) + v
    return total


def diff(after, before):
    return {k: v - before.get(k, 0.0) for k, v in after.items()}


def metric(data, name, **labels):
    want = ",".join(f'{k}="{v}"' for k, v in labels.items())
    return sum(v for k, v in data.items() if k.startswith(name) and (not want or all(
        f'{k2}="{v2}"' in k for k2, v2 in labels.items())))


def histogram_quantile(data, step, q):
    buckets = []
    for k, v in data.items():
        if not k.startswith("worker_task_duration_seconds_bucket{"):
            continue
        labels = dict(re.findall(r'(\w+)="([^"]*)"', k))
        if labels.get("step") == step:
            buckets.append((float(labels["le"]), v))
    buckets.sort()
    total = buckets[-1][1]
    if total == 0:
        return None
    rank = q * total
    prev_le, prev_c = 0.0, 0.0
    for le, c in buckets:
        if c >= rank:
            if math.isinf(le):
                return prev_le
            return prev_le + (le - prev_le) * (rank - prev_c) / (c - prev_c)
        prev_le, prev_c = le, c


def step_mean(data, step):
    count = data.get(f'worker_task_duration_seconds_count{{step="{step}"}}', 0)
    return data.get(f'worker_task_duration_seconds_sum{{step="{step}"}}', 0) / count if count else None


def lock_sampler(stop, out):
    while not stop.is_set():
        out.append(int(sql("SELECT count(*) FROM pg_stat_activity WHERE wait_event_type = 'Lock'")))
        stop.wait(1)


def db_counters():
    row = sql_one("SELECT xact_commit + xact_rollback AS xacts FROM pg_stat_database WHERE datname = current_database()")
    usage = sh("kubectl", "exec", "db-0", "--", "sh", "-c", "grep usage_usec /sys/fs/cgroup/cpu.stat")
    return {"t": time.time(), "xacts": row["xacts"], "cpu_usec": int(usage.split()[1])}


def db_sampler(stop, out):
    p = subprocess.Popen(["kubectl", "exec", "-i", "db-0", "--", "sh", "-c",
                          'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -At -F " " -v ON_ERROR_STOP=1'],
                         stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1)
    query = ("SELECT extract(epoch FROM clock_timestamp()), xact_commit + xact_rollback, "
             "substring(pg_read_file('/sys/fs/cgroup/cpu.stat') FROM 'usage_usec ([0-9]+)') "
             "FROM pg_stat_database WHERE datname = current_database();\n")
    while not stop.is_set():
        p.stdin.write(query)
        t, xacts, usec = p.stdout.readline().split()
        out.append({"t": float(t), "xacts": int(xacts), "cpu_usec": int(usec)})
        stop.wait(0.2)
    p.stdin.close()
    p.wait()


def duplicates(logs):
    table = sql_one("SELECT count(*) AS dup FROM (SELECT task_id FROM task_results GROUP BY task_id HAVING count(*) > 1) d")["dup"]
    done = {}
    for line in logs.lines(" done task="):
        tid = re.search(r"task=(\d+)", line).group(1)
        done[tid] = done.get(tid, 0) + 1
    by_logs = sum(1 for v in done.values() if v > 1)
    n = int(sql("SELECT count(*) FROM tasks"))
    return {"n": n, "n_dup_table": table, "D_table": table / n, "n_dup_logs": by_logs, "D_logs": by_logs / n,
            "results_rows": int(sql("SELECT count(*) FROM task_results"))}


def step1(args):
    reset()
    set_workers(0)
    ids = json.load(open(os.path.join(RESULTS, "ids.json")))
    out = []

    def curl(method, path):
        p = subprocess.run(["curl", "-s", "-X", method, "-w", "\\nHTTP %{http_code}", f"http://localhost:8080{path}"],
                           capture_output=True, text=True)
        out.append(f"$ curl -X {method} {path}\n{p.stdout}\n")
        return p.stdout

    body = curl("POST", f"/donations/{ids[0]}/tasks")
    task_id = json.loads(body.rsplit("\n", 1)[0])["id"]
    curl("POST", "/donations/999999/tasks")
    curl("GET", f"/tasks/{task_id}")
    curl("GET", "/tasks/999999")
    curl("GET", "/tasks?status=queued&limit=2&offset=0")
    time.sleep(6)
    metrics = sh("curl", "-s", "http://localhost:8080/metrics")
    out.append("$ curl /metrics | grep ^tasks_\n" + "\n".join(l for l in metrics.splitlines() if l.startswith("tasks_")) + "\n")
    set_workers(1, **FINAL)
    wait_empty()
    curl("GET", f"/tasks/{task_id}")
    out.append("$ \\d tasks\n" + sh("kubectl", "exec", "db-0", "--", "sh", "-c", 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "\\d tasks" -c "\\d task_results"'))
    with open(os.path.join(RESULTS, "step1_endpoints.txt"), "w") as f:
        f.write("\n".join(out))
    print("\n".join(out))


def step2(args):
    reset()
    pods = set_workers(1, **FINAL)
    logs = Logs("step2")
    before = scrape_all(pods)
    generate("step2/tasks.csv", 100, count=20).wait()
    wait_empty()
    logs.close()
    d = diff(scrape_all(pods), before)
    fetch, process, store, claim = (step_mean(d, s) for s in ("fetch", "process", "store", "claim"))
    save("step2/summary.json", {
        "tasks_done": metric(d, "worker_tasks_total", result="done"),
        "t_fetch_s": fetch, "t_process_s": process, "t_store_s": store, "t_claim_s": claim,
        "t_io_s": fetch + store, "t_cpu_s": process,
        "metric_names": sorted({k.split("{")[0] for k in d if k.startswith("worker_")}),
    })


def series(folder, rate, count, lock_sample=False):
    reset()
    pods = [p["metadata"]["name"] for p in worker_pods()]
    logs = Logs(folder)
    before = scrape_all(pods)
    stop, locks = threading.Event(), []
    sampler = threading.Thread(target=lock_sampler, args=(stop, locks)) if lock_sample else None
    if sampler:
        sampler.start()
    t0 = time.time()
    generate(f"{folder}/tasks.csv", rate, count=count).wait()
    wait_empty()
    elapsed = time.time() - t0
    after = scrape_all(pods)
    stop.set()
    if sampler:
        sampler.join()
    logs.close()
    d = diff(after, before)
    result = duplicates(logs)
    result.update({
        "elapsed_s": elapsed,
        "claims_task": metric(d, "worker_claims_total", result="task"),
        "claims_empty": metric(d, "worker_claims_total", result="empty"),
        "claim_p99_ms": histogram_quantile(d, "claim", 0.99) * 1000,
        "claim_mean_ms": step_mean(d, "claim") * 1000,
    })
    if lock_sample:
        result["lock_waits_max"] = max(locks)
        result["lock_waits_samples"] = locks
    return result


def step3(args):
    sql("ALTER TABLE task_results DROP CONSTRAINT IF EXISTS task_results_pkey; "
        "CREATE INDEX IF NOT EXISTS task_results_task_id_idx ON task_results (task_id)")
    out = {}
    for poll in (50, 1):
        set_workers(R, CLAIM="naive", SAFE="0", WAIT="poll", T_POLL_MS=str(poll), FAIL_RATE="0")
        out[f"poll_{poll}ms"] = series(f"step3/poll_{poll}ms", 100, 300)
    save("step3/summary.json", out)


def step4(args):
    reset()
    sql("DROP INDEX IF EXISTS task_results_task_id_idx; "
        "ALTER TABLE task_results ADD CONSTRAINT task_results_pkey PRIMARY KEY (task_id)")
    out = {}
    for claim in ("skip_locked", "for_update"):
        set_workers(R, CLAIM=claim, SAFE="0", WAIT="poll", T_POLL_MS="1", FAIL_RATE="0")
        out[claim] = series(f"step4/{claim}", 100, 300, lock_sample=True)
    save("step4/summary.json", out)
    set_workers(R, **FINAL)


def task_row(tid):
    return sql_one(f"SELECT id, status, attempts, token, worker_id, lease_until, started_at, finished_at FROM tasks WHERE id = {tid}")


def step5(args):
    for attempt in range(5):
        try:
            return lease_check()
        except LookupError:
            note(f"step 5 attempt {attempt + 1}: no task caught in processing, repeating")


def lease_check():
    reset()
    pods = set_workers(2, **FINAL)
    logs = Logs("step5")
    gen = generate("step5/tasks.csv", 30, count=100)
    time.sleep(0.5)
    victim, task, before = None, None, None
    while gen.poll() is None:
        pod = pods[0]
        sh("kubectl", "exec", pod, "--", "pkill", "-STOP", "-f", "worker.py")
        t_stop = time.time()
        time.sleep(0.3)
        row = sql_one(f"SELECT id FROM tasks WHERE status = 'running' AND worker_id = '{pod}'")
        if row:
            victim, task, before = pod, row["id"], task_row(row["id"])
            break
        sh("kubectl", "exec", pod, "--", "pkill", "-CONT", "-f", "worker.py")
    if victim is None:
        gen.wait()
        wait_empty()
        logs.close()
        raise LookupError
    note(f"stopped {victim} with task {task}")
    while time.time() < t_stop + 2 * LEASE_S:
        time.sleep(0.2)
    sh("kubectl", "exec", victim, "--", "pkill", "-CONT", "-f", "worker.py")
    t_cont = time.time()
    gen.wait()
    wait_empty()
    time.sleep(3)
    logs.close()
    after = task_row(task)
    results = sql_json(f"SELECT task_id, token, worker_id, created_at FROM task_results WHERE task_id = {task}")
    save("step5/summary.json", {
        "stopped_worker": victim, "task_id": task, "stop_unix": t_stop, "cont_unix": t_cont,
        "before": before, "after": after, "completed_by": [r["worker_id"] for r in results],
        "results_rows": len(results), "results": results,
        "victim_zero_rows_lines": [l for l in logs.lines("0 rows") if victim in l],
        "victim_task_lines": [l for l in logs.lines(f"task={task} ")],
        "all_done": int(sql("SELECT count(*) FROM tasks WHERE status = 'done'")),
        "tasks_with_many_results": sql_one("SELECT count(*) AS n FROM (SELECT task_id FROM task_results GROUP BY task_id HAVING count(*) > 1) d")["n"],
    })


def per_second(times):
    if not times:
        return []
    start = int(min(times))
    counts = [0] * (int(max(times)) - start + 1)
    for t in times:
        counts[int(t) - start] += 1
    return counts


def step6(args):
    out = {}
    for w in (1, 2, R):
        reset()
        set_workers(0, **FINAL)
        generate(f"step6/w{w}_tasks.csv", 200, count=400).wait()
        stop, samples = threading.Event(), []
        sampler = threading.Thread(target=db_sampler, args=(stop, samples))
        sampler.start()
        pods = set_workers(w)
        m_before = scrape_all(pods)
        wait_empty()
        stop.set()
        sampler.join()
        d = diff(scrape_all(pods), m_before)
        rows = sql_json("SELECT worker_id AS w, extract(epoch FROM started_at) AS s, extract(epoch FROM finished_at) AS f FROM tasks")
        finished = [r["f"] for r in rows]
        first_start, last = min(r["s"] for r in rows), max(finished)
        all_started = max(min(r["s"] for r in rows if r["w"] == wid) for wid in {r["w"] for r in rows})
        window_start = max(all_started, last - 30)
        in_window = [f for f in finished if f >= window_start]
        per_sec = per_second(in_window)
        duration = last - window_start
        inside = [x for x in samples if window_start <= x["t"] <= last]
        before, after = inside[0], inside[-1]
        db_time = after["t"] - before["t"]
        out[f"W={w}"] = {
            "W": w, "tasks": len(rows), "processing_s": last - first_start, "all_workers_started_after_s": all_started - first_start,
            "window_s": duration, "tasks_in_window": len(in_window),
            "X": len(in_window) / duration, "X_std_per_second": statistics.pstdev(per_sec[1:-1] if len(per_sec) > 3 else per_sec),
            "per_second": per_sec,
            "claim_p99_ms": histogram_quantile(d, "claim", 0.99) * 1000,
            "db_tps": (after["xacts"] - before["xacts"] - (len(inside) - 1)) / db_time,
            "db_cpu_cores": (after["cpu_usec"] - before["cpu_usec"]) / 1e6 / db_time,
            "db_sample_span_s": db_time,
            "t_fetch_s": step_mean(d, "fetch"), "t_process_s": step_mean(d, "process"), "t_store_s": step_mean(d, "store"),
        }
        note(f"W={w} X={out[f'W={w}']['X']:.2f}")
    save("step6/summary.json", out)


def created_window(start, end):
    return sql_json(
        "SELECT extract(epoch FROM created_at) AS c, extract(epoch FROM started_at) AS s FROM tasks "
        f"WHERE created_at >= to_timestamp({start}) AND created_at < to_timestamp({end}) ORDER BY created_at")


def x_r():
    return json.load(open(os.path.join(RESULTS, "step6", "summary.json")))[f"W={R}"]["X"]


def step7(args):
    xr = x_r()
    reset()
    set_workers(R, **FINAL)
    rate = round(0.7 * xr, 2)
    t0 = time.time()
    generate("step7/steady_tasks.csv", rate, duration=180).wait()
    t_end = t0 + 0.2 + 180
    while time.time() < t_end + 12:
        time.sleep(1)
    w_start = t_end - 120
    rows = created_window(w_start, t_end)
    lam = len(rows) / 120
    wq = statistics.mean(r["s"] - r["c"] for r in rows)
    lq = prom("avg_over_time(sum(tasks_queued)[120s:5s])", at=t_end)
    lq = float(lq[0]["value"][1])
    steady = {"rate_target": rate, "lambda": lam, "W_q_s": wq, "lambda_W_q": lam * wq, "L_q_measured": lq,
              "diff_pct": (lq - lam * wq) / (lam * wq) * 100 if lam * wq else None,
              "window_unix": [w_start, t_end]}
    wait_empty()
    reset()
    rate = round(1.3 * xr, 2)
    t0 = time.time()
    generate("step7/overload_tasks.csv", rate, duration=120).wait()
    queue_end = int(sql("SELECT count(*) FROM tasks WHERE status = 'queued'"))
    wait_empty()
    rows = sql_json("SELECT extract(epoch FROM created_at) AS c, extract(epoch FROM started_at) AS s FROM tasks ORDER BY created_at")
    first = [r["s"] - r["c"] for r in rows[:100]]
    last = [r["s"] - r["c"] for r in rows[-100:]]
    save("step7/summary.json", {"steady": steady, "overload": {
        "rate_target": rate, "created": len(rows), "queue_at_end": queue_end,
        "W_q_first_100_s": statistics.mean(first), "W_q_last_100_s": statistics.mean(last)}})


class PodWatch:
    def __init__(self, path):
        self.f = open(path, "w")
        template = ('{.metadata.name}{" "}{.status.containerStatuses[0].state.terminated.exitCode}{" "}'
                    '{.status.containerStatuses[0].state.terminated.reason}{" "}'
                    '{.status.containerStatuses[0].lastState.terminated.exitCode}{"\\n"}')
        self.p = subprocess.Popen(["kubectl", "get", "pods", "-l", "app=worker", "-w", "-o", f"jsonpath={template}"],
                                  stdout=self.f, stderr=subprocess.DEVNULL)

    def close(self):
        self.p.terminate()
        self.f.close()


def summary_counts():
    return sql_one(
        "SELECT (SELECT count(*) FROM tasks) AS total, "
        "(SELECT count(*) FROM tasks WHERE status = 'done') AS done, "
        "(SELECT count(*) FROM tasks WHERE status = 'failed') AS failed, "
        "(SELECT count(*) FROM tasks WHERE attempts > 1) AS attempts_gt_1, "
        "(SELECT count(*) FROM (SELECT task_id FROM task_results GROUP BY task_id HAVING count(*) > 1) d) AS many_results")


def step8(args):
    xr = x_r()
    reset()
    set_workers(R, **FINAL)
    logs = Logs("step8")
    watch = PodWatch(os.path.join(RESULTS, "step8", "pod_watch.txt"))
    gen = generate("step8/tasks.csv", round(0.7 * xr, 2), duration=90)
    time.sleep(30)
    t_restart = time.time()
    sh("kubectl", "rollout", "restart", "deployment/worker")
    sh("kubectl", "rollout", "status", "deployment/worker", "--timeout=300s")
    gen.wait()
    wait_empty()
    time.sleep(3)
    watch.close()
    logs.close()
    codes = {}
    for line in open(os.path.join(RESULTS, "step8", "pod_watch.txt")):
        parts = line.split()
        if len(parts) >= 2 and parts[1].isdigit():
            codes[parts[0]] = {"exit_code": int(parts[1]), "reason": parts[2] if len(parts) > 2 else None}
    counts = summary_counts()
    counts.update({"restart_unix": t_restart, "exit_codes": codes,
                   "requeued_by_workers": len(logs.lines("requeued on shutdown")),
                   "sigterm_lines": logs.lines("SIGTERM"), "exit_lines": logs.lines("exit 0")})
    save("step8/summary.json", counts)


def step9(args):
    xr = x_r()
    reset()
    set_workers(R, **dict(FINAL, FAIL_RATE="0.15"))
    restarts_before = {p["metadata"]["name"]: p["status"]["containerStatuses"][0]["restartCount"] for p in worker_pods()}
    logs = Logs("step9")
    t0 = time.time()
    gen = generate("step9/tasks.csv", round(0.7 * xr, 2), duration=300)
    while time.time() < t0 + 150:
        time.sleep(0.5)
    scenario = None
    for _ in range(300):
        scenario = sql_one(
            "WITH t AS (SELECT id, worker_id FROM tasks WHERE status = 'running' ORDER BY started_at DESC LIMIT 1) "
            "SELECT t.id AS task_id, t.worker_id, a.pid, a.application_name, a.state, "
            "pg_terminate_backend(a.pid) AS terminated, extract(epoch FROM clock_timestamp()) AS at "
            "FROM t JOIN pg_stat_activity a ON a.application_name = t.worker_id || '/main'")
        if scenario:
            break
        time.sleep(0.1)
    note(f"terminated {scenario}")
    gen.wait()
    t_gen_end = time.time()
    wait_empty(timeout=3600)
    t_end = time.time()
    time.sleep(3)
    pods_text = sh("kubectl", "get", "pods", "-l", "app=worker", "-o", "wide")
    logs.close()
    tid, wid = scenario["task_id"], scenario["worker_id"]
    dist = sql_json("SELECT attempts, status, count(*) AS n FROM tasks GROUP BY attempts, status ORDER BY attempts, status")
    counts = summary_counts()
    rows = sql_json("SELECT extract(epoch FROM finished_at) AS f FROM tasks WHERE status = 'done'")
    done_in_gen = [r["f"] for r in rows if t0 + 30 <= r["f"] < t_gen_end]
    restarts = {p["metadata"]["name"]: p["status"]["containerStatuses"][0]["restartCount"] for p in worker_pods()}
    save("step9/summary.json", {
        "rate": round(0.7 * xr, 2), "X_R_no_failures": xr, "t0": t0, "t_gen_end": t_gen_end, "t_drained": t_end,
        "X_run": len(done_in_gen) / (t_gen_end - t0 - 30),
        "restarts": restarts, "restarts_before": restarts_before, "restarts_total": sum(restarts.values()),
        "kubectl_get_pods": pods_text, "attempts_distribution": dist, "counts": counts,
        "share_attempts_gt_1": counts["attempts_gt_1"] / counts["total"], "share_failed": counts["failed"] / counts["total"],
        "f": 0.15, "f3": 0.15 ** 3,
        "scenario": scenario, "scenario_task": task_row(tid),
        "scenario_task_results": sql_json(f"SELECT task_id, token, worker_id, created_at FROM task_results WHERE task_id = {tid}"),
        "scenario_worker_lines": [l for l in logs.lines(wid) if "connection" in l or f"task={tid} " in l],
        "crash_lines": len(logs.lines(" crash task=")),
    })
    series_q = {name: prom_range(f"sum({name})", t0 - 10, t_end + 10)
                for name in ("tasks_queued", "tasks_running")}
    finished = sql_json("SELECT extract(epoch FROM finished_at) AS f FROM tasks WHERE status = 'done'")
    save("step9/timeline.json", {"prom": series_q, "finished": sorted(r["f"] for r in finished),
                                 "scenario_at": scenario["at"], "t0": t0, "t_gen_end": t_gen_end})


def idle_rate(pods, seconds=30):
    before_db, before_w = db_counters(), scrape_all(pods)
    time.sleep(seconds)
    after_db, after_w = db_counters(), scrape_all(pods)
    d = diff(after_w, before_w)
    span = after_db["t"] - before_db["t"]
    return {"worker_claims_per_s": (metric(d, "worker_claims_total", result="empty") + metric(d, "worker_claims_total", result="task")) / span,
            "db_xacts_per_s": (after_db["xacts"] - before_db["xacts"]) / span}


def step10(args):
    sql(open(os.path.join(ROOT, "db", "notify.sql")).read())
    out = {"trigger": sql("SELECT tgname FROM pg_trigger WHERE tgrelid = 'tasks'::regclass AND NOT tgisinternal")}
    for mode, env in (("poll_100ms", {"WAIT": "poll", "T_POLL_MS": "100"}), ("listen", {"WAIT": "listen", "T_POLL_MS": "100"})):
        reset()
        pods = set_workers(R, **dict(FINAL, **env))
        logs = Logs(f"step10/{mode}")
        time.sleep(5)
        idle = idle_rate(pods, 30)
        generate(f"step10/{mode}_tasks.csv", 5, duration=60).wait()
        wait_empty()
        logs.close()
        delays = sorted(sql_json("SELECT extract(epoch FROM started_at - created_at) AS d FROM tasks"), key=lambda r: r["d"])
        values = [r["d"] for r in delays]
        pick = lambda q: values[max(0, math.ceil(q * len(values)) - 1)]
        out[mode] = {"tasks": len(values), "p50_ms": pick(0.5) * 1000, "p99_ms": pick(0.99) * 1000,
                     "mean_ms": statistics.mean(values) * 1000, "idle": idle}
    save("step10/summary.json", out)
    set_workers(R, **FINAL)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("steps", nargs="+")
    args = p.parse_args()
    for s in args.steps:
        note(f"step {s} start")
        globals()[f"step{s}"](args)
        note(f"step {s} done")


if __name__ == "__main__":
    main()
