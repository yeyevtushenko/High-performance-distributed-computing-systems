import csv
import json
import math
import os
import sys
import urllib.parse
import urllib.request

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

PROM = "http://localhost:9090"
R = "results"
CHARTS = os.path.join(R, "charts")
NS = (1, 2, 3, 4)
BUCKET = "http_request_duration_seconds_bucket"
READ_PATH = '{path="/donations/{donation_id}"}'


def prom(endpoint, **params):
    url = f"{PROM}/api/v1/{endpoint}?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url) as resp:
        return json.load(resp)["data"]["result"]


def instant(expr, t):
    return prom("query", query=expr, time=t)


def series(expr, start, end, step=5):
    return prom("query_range", query=expr, start=start, end=end, step=step)


def quantile(buckets, q):
    finite = sorted((float(le), c) for le, c in buckets.items() if le != "+Inf")
    n = buckets.get("+Inf", 0.0)
    if n <= 0:
        return None
    r = q * n
    prev_b, prev_c = 0.0, 0.0
    for b, c in finite:
        if c >= r and c > prev_c:
            value = prev_b + (b - prev_b) * (r - prev_c) / (c - prev_c)
            return {"value_ms": value * 1000, "bucket_ms": (prev_b * 1000, b * 1000), "error_ms": (b - prev_b) * 1000, "beyond": False}
        prev_b, prev_c = b, c
    return {"value_ms": prev_b * 1000, "bucket_ms": (prev_b * 1000, math.inf), "error_ms": math.inf, "beyond": True}


def server_p99(t, window=15):
    rows = instant(f"sum by (le) (increase({BUCKET}[{window}s]))", t)
    return quantile({r["metric"]["le"]: float(r["value"][1]) for r in rows}, 0.99)


def scalar(expr, t):
    rows = instant(expr, t)
    return float(rows[0]["value"][1]) if rows else None


def per_instance(expr, t):
    return {r["metric"]["instance"]: float(r["value"][1]) for r in instant(expr, t)}


def load(name):
    with open(os.path.join(R, f"{name}_steps.json")) as f:
        summary = json.load(f)
    with open(os.path.join(R, f"{name}_seconds.csv")) as f:
        seconds = list(csv.DictReader(f))
    return summary, seconds


def step_metrics(step):
    t = step["end_unix"]
    return {
        "server_p99": server_p99(t),
        "cpu_per_instance": scalar('avg(rate(process_cpu_seconds_total{job="web"}[15s]))', t),
        "in_progress_per_instance": scalar("avg(avg_over_time(http_requests_in_progress[15s]))", t),
        "throttling": per_instance("increase(cgroup_cpu_nr_throttled_total[15s]) / increase(cgroup_cpu_nr_periods_total[15s])", t),
    }


def fit_usl(points):
    u = [n - 1 for n, _ in points]
    v = [n * (n - 1) for n, _ in points]
    y = [n / c - 1 for n, c in points]
    suu = sum(a * a for a in u)
    svv = sum(a * a for a in v)
    suv = sum(a * b for a, b in zip(u, v))
    suy = sum(a * b for a, b in zip(u, y))
    svy = sum(a * b for a, b in zip(v, y))
    det = suu * svv - suv ** 2
    sigma = (svv * suy - suv * svy) / det
    kappa = (suu * svy - suv * suy) / det
    if sigma < 0:
        sigma, kappa = 0.0, svy / svv
    elif kappa < 0:
        sigma, kappa = suy / suu, 0.0
    return {"sigma": sigma, "kappa": kappa, "y": dict(zip([n for n, _ in points], y)),
            "sums": {"Σu²": suu, "Σv²": svv, "Σuv": suv, "Σuy": suy, "Σvy": svy}}


def usl(n, lam, sigma, kappa):
    return lam * n / (1 + sigma * (n - 1) + kappa * n * (n - 1))


def run_chart(n, summary, seconds):
    t0 = summary["t0_unix"]
    start, end = t0, float(seconds[-1]["unix"]) + 1
    xs = [float(r["unix"]) - t0 for r in seconds]
    fig, axes = plt.subplots(5, 1, figsize=(11, 15), sharex=True)
    axes[0].plot(xs, [float(r["target_rps"]) for r in seconds], label="задана")
    axes[0].plot(xs, [float(r["achieved"]) for r in seconds], label="досягнута")
    axes[0].set_ylabel("запитів/с")
    axes[0].legend()
    axes[1].plot(xs, [float(r["p99_ms"]) if r["p99_ms"] else math.nan for r in seconds], label="p99 клієнта, за секунду")
    rows = series(f"sum by (le) (increase({BUCKET}[15s]))", start, end)
    by_t = {}
    for r in rows:
        for t, v in r["values"]:
            by_t.setdefault(float(t), {})[r["metric"]["le"]] = float(v)
    ts = sorted(by_t)
    p99s = [quantile(by_t[t], 0.99) for t in ts]
    axes[1].plot([t - t0 for t in ts], [p["value_ms"] if p else math.nan for p in p99s], label="p99 сервера, вікно 15 с, крок 5 с")
    axes[1].set_yscale("log")
    axes[1].set_ylabel("мс")
    axes[1].legend()
    for ax, expr, label in (
        (axes[2], f"sum by (instance) (rate(http_requests_total{READ_PATH}[15s]))", "запитів/с"),
        (axes[3], 'rate(process_cpu_seconds_total{job="web"}[15s])', "CPU, ядер"),
        (axes[4], "http_requests_in_progress", "запитів в обробці"),
    ):
        for r in series(expr, start, end):
            ax.plot([float(t) - t0 for t, _ in r["values"]], [float(v) for _, v in r["values"]], label=r["metric"]["instance"])
        ax.set_ylabel(label)
        ax.legend(fontsize=8)
    axes[4].set_xlabel("час від початку прогону, с")
    fig.suptitle(f"Ступінчастий прогін, {n} екз.")
    fig.tight_layout()
    fig.savefig(os.path.join(CHARTS, f"run_n{n}.png"), dpi=110)
    plt.close(fig)


def sampler_at(n, a, b):
    rows = {}
    with open(os.path.join(R, f"sampler_n{n}.txt")) as f:
        for line in f:
            ts, name, rest = line.split(" ", 2)
            if a <= int(ts) <= b:
                rows.setdefault(name, []).append((int(ts), rest.strip()))
    return rows


def main():
    os.makedirs(CHARTS, exist_ok=True)
    noop, _ = load("noop")
    G = noop["X"]
    out = {"G": G, "noop_steps": noop["steps"], "runs": {}}
    for n in NS:
        summary, seconds = load(f"step_n{n}")
        best = next(s for s in summary["steps"] if s["step"] == summary["X_step"])
        limited = summary["X"] >= 0.9 * G or summary["last_step_achieved_ratio"] >= 0.9
        run = {"X": summary["X"], "X_std": summary["X_std"], "X_step": summary["X_step"], "X_target": summary["X_target_rps"],
               "limited_by_generator": limited, "best": best, "best_metrics": step_metrics(best), "steps": summary["steps"]}
        if n in (1, 4):
            run["throttling_by_step"] = [{"target": s["target_rps"], "throttling": step_metrics(s)["throttling"]} for s in summary["steps"]]
        if n == 1:
            run["p99_by_step"] = [{"target": s["target_rps"], "client_ms": s["p99_ms"], "server": server_p99(s["end_unix"])} for s in summary["steps"]]
        if n == 4:
            run["sampler"] = sampler_at(4, int(best["tail_start_unix"]), int(best["end_unix"]))
        out["runs"][n] = run
        if n in (1, 4):
            run_chart(n, summary, seconds)
    X1 = out["runs"][1]["X"]
    points = [(n, out["runs"][n]["X"] / X1) for n in NS if n > 1 and not out["runs"][n]["limited_by_generator"]]
    out["C"] = {n: out["runs"][n]["X"] / X1 for n in NS}
    if len(points) >= 2:
        fit = fit_usl(points)
        fit["N_star"] = math.sqrt((1 - fit["sigma"]) / fit["kappa"]) if fit["kappa"] > 0 and fit["sigma"] < 1 else None
        out["usl"] = fit
        fig, ax = plt.subplots(figsize=(8, 5))
        ns = [n / 10 for n in range(10, 81)]
        ax.errorbar(list(NS), [out["runs"][n]["X"] for n in NS], yerr=[out["runs"][n]["X_std"] for n in NS], fmt="o", capsize=4, label="виміряна X(N)")
        ax.plot(ns, [usl(n, X1, fit["sigma"], fit["kappa"]) for n in ns], label=f"USL: σ = {fit['sigma']:.3f}, κ = {fit['kappa']:.4f}")
        ax.plot(ns, [X1 * n for n in ns], "--", label="λN, λ = X(1)")
        ax.set_xlabel("кількість екземплярів N")
        ax.set_ylabel("пропускна здатність, запитів/с")
        ax.set_ylim(0, max(out["runs"][n]["X"] for n in NS) * 2)
        ax.legend()
        fig.tight_layout()
        fig.savefig(os.path.join(CHARTS, "usl.png"), dpi=110)
        plt.close(fig)
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.5), sharey=True)
    for ax, n in zip(axes, (1, 4)):
        steps = out["runs"][n]["throttling_by_step"]
        instances = sorted({i for s in steps for i in s["throttling"]})
        for i in instances:
            ax.plot([s["target"] for s in steps], [s["throttling"].get(i, math.nan) for s in steps], "o-", label=i)
        ax.set_title(f"{n} екз.")
        ax.set_xlabel("задана інтенсивність, запитів/с")
        ax.legend(fontsize=8)
    axes[0].set_ylabel("частка дроселювання CPU")
    fig.tight_layout()
    fig.savefig(os.path.join(CHARTS, "throttling.png"), dpi=110)
    plt.close(fig)
    with open(os.path.join(R, "analysis.json"), "w") as f:
        json.dump(out, f, indent=2, ensure_ascii=False, default=str)
    print(json.dumps({k: out[k] for k in ("G", "C")}, default=str))
    for n in NS:
        r = out["runs"][n]
        print(n, r["X"], r["X_std"], r["X_target"], r["limited_by_generator"], json.dumps(r["best_metrics"], default=str))
    print(json.dumps(out.get("usl"), default=str))


if __name__ == "__main__":
    sys.exit(main())
