import csv
import json
import math
import os
import urllib.parse
import urllib.request

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

PROM = "http://localhost:9090"
R = "results"
CHARTS = os.path.join(R, "charts")
NS = (1, 2, 3, 5)
RMAX = 5
REQ_CPU = 0.25
READ = 'job="web",path="/donations/{donation_id}"'
BUCKET = 'http_request_duration_seconds_bucket{job="web"}'
LAB2 = "../Lab2/results/analysis.json"


def prom(endpoint, **params):
    with urllib.request.urlopen(f"{PROM}/api/v1/{endpoint}?" + urllib.parse.urlencode(params)) as resp:
        return json.load(resp)["data"]["result"]


def instant(expr, t):
    return prom("query", query=expr, time=t)


def series(expr, start, end, step=1):
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
            return {"value_ms": (prev_b + (b - prev_b) * (r - prev_c) / (c - prev_c)) * 1000,
                    "bucket_ms": (prev_b * 1000, b * 1000), "beyond": False}
        prev_b, prev_c = b, c
    return {"value_ms": prev_b * 1000, "bucket_ms": (prev_b * 1000, math.inf), "beyond": True}


def server_p99(t, by=None, window=20):
    group = f"le, {by}" if by else "le"
    rows = instant(f"sum by ({group}) (increase({BUCKET}[{window}s]))", t)
    out = {}
    for r in rows:
        key = r["metric"].get(by) if by else "all"
        out.setdefault(key, {})[r["metric"]["le"]] = float(r["value"][1])
    res = {k: quantile(v, 0.99) for k, v in out.items()}
    return res if by else res.get("all")


def by_label(expr, t, label):
    return {r["metric"][label]: float(r["value"][1]) for r in instant(expr, t)}


def throttling(t, window=30):
    return by_label(f'increase(container_cpu_cfs_throttled_periods_total{{container="web"}}[{window}s]) / '
                    f'increase(container_cpu_cfs_periods_total{{container="web"}}[{window}s])', t, "pod")


def load(name):
    with open(os.path.join(R, f"{name}_steps.json")) as f:
        summary = json.load(f)
    with open(os.path.join(R, f"{name}_seconds.csv")) as f:
        seconds = list(csv.DictReader(f))
    return summary, seconds


def fit_usl(points):
    u = [n - 1 for n, _ in points]
    v = [n * (n - 1) for n, _ in points]
    y = [n / c - 1 for n, c in points]
    suu, svv = sum(a * a for a in u), sum(a * a for a in v)
    suv = sum(a * b for a, b in zip(u, v))
    suy, svy = sum(a * b for a, b in zip(u, y)), sum(a * b for a, b in zip(v, y))
    det = suu * svv - suv ** 2
    raw_sigma = (svv * suy - suv * svy) / det
    raw_kappa = (suu * svy - suv * suy) / det
    sigma, kappa = raw_sigma, raw_kappa
    if sigma < 0:
        sigma, kappa = 0.0, svy / svv
    elif kappa < 0:
        sigma, kappa = suy / suu, 0.0
    n_star = math.sqrt((1 - sigma) / kappa) if kappa > 0 and sigma < 1 else None
    return {"sigma": sigma, "kappa": kappa, "raw_sigma": raw_sigma, "raw_kappa": raw_kappa, "N_star": n_star,
            "y": dict(zip([n for n, _ in points], y)), "sums": {"suu": suu, "svv": svv, "suv": suv, "suy": suy, "svy": svy}}


def usl(n, lam, sigma, kappa):
    return lam * n / (1 + sigma * (n - 1) + kappa * n * (n - 1))


def ready_series(start, end):
    rows = series('kube_deployment_status_replicas_ready{deployment="web"}', start, end)
    return {int(float(t)): float(v) for t, v in rows[0]["values"]} if rows else {}


def desired_series(start, end):
    rows = series('kube_horizontalpodautoscaler_status_desired_replicas{horizontalpodautoscaler="web"}', start, end)
    return {int(float(t)): float(v) for t, v in rows[0]["values"]} if rows else {}


def cpu_series(start, end):
    rows = series(f'avg(rate(process_cpu_seconds_total{{job="web"}}[15s])) / {REQ_CPU}', start, end)
    return {int(float(t)): float(v) for t, v in rows[0]["values"]} if rows else {}


def elastic(name, X, p99_0, spike_p99_fixed):
    summary, seconds = load(name)
    t0 = summary["t0_unix"]
    segs = summary["segments"]
    T = int(segs[-1]["end_s"])
    rows = {int(r["second"]): r for r in seconds}
    start, end = int(t0), int(t0) + T
    ready = ready_series(start - 10, end + 5)
    desired = desired_series(start - 10, end + 5)
    cpu = cpu_series(start, end)

    def at(m, ts):
        keys = [k for k in m if k <= ts]
        return m[max(keys)] if keys else None

    out = []
    for s in range(T):
        z = summary["profile"]["segments"][0][1] if s < segs[0]["end_s"] else (
            summary["profile"]["segments"][1][1] if s < segs[1]["end_s"] else summary["profile"]["segments"][2][1])
        d = next((n for n in range(1, RMAX + 1) if X[n] >= z), RMAX)
        r = rows.get(s, {})
        out.append({"s": s, "z": z, "d": d, "s_ready": at(ready, start + s), "desired": at(desired, start + s),
                    "achieved": float(r.get("achieved") or 0), "p99": float(r["p99_ms"]) if r.get("p99_ms") else None,
                    "cpu_rel": at(cpu, start + s)})
    su = [x["s_ready"] for x in out]
    metrics = {
        "T": T,
        "theta_U": sum(1 for x in out if x["s_ready"] < x["d"]) / T,
        "theta_O": sum(1 for x in out if x["s_ready"] > x["d"]) / T,
        "a_U": sum(max(x["d"] - x["s_ready"], 0) for x in out) / T,
        "a_O": sum(max(x["s_ready"] - x["d"], 0) for x in out) / T,
        "W": sum(su),
        "violations": sum(1 for x in out if x["p99"] is not None and x["p99"] > 2 * p99_0),
        "non_2xx": sum(sg["non_2xx"] for sg in segs),
        "conn_errors": sum(sg["conn_errors"] for sg in segs),
    }
    spike_start, spike_end = int(segs[1]["begin_s"]), int(segs[1]["end_s"])
    if desired:
        base = out[spike_start - 1]["desired"]
        first = next((x["s"] for x in out[spike_start:] if x["desired"] is not None and x["desired"] != base), None)
        metrics["t_first_desired_change"] = None if first is None else first - spike_start
        max_ready = max(x["s_ready"] for x in out)
        reach = next((x["s"] for x in out[spike_start:] if x["s_ready"] == max_ready), None)
        metrics["max_ready"] = max_ready
        metrics["t_max_ready"] = None if reach is None or reach >= spike_end else reach - spike_start
        limit = 1.2 * spike_p99_fixed
        run_len, stable = 0, None
        for x in out[spike_start:spike_end]:
            run_len = run_len + 1 if x["p99"] is not None and x["p99"] < limit else 0
            if run_len == 10:
                stable = x["s"] - 9 - spike_start
                break
        metrics["t_p99_stable"] = stable
        before = out[spike_end - 1]["s_ready"]
        dec = next((x["s"] for x in out[spike_end:] if x["s_ready"] < before), None)
        metrics["t_first_decrease"] = None if dec is None else dec - spike_end
    return out, metrics, segs


def chart_elastic(fixed, hpa):
    fig, axes = plt.subplots(4, 2, figsize=(13, 13), sharex=True, sharey="row")
    for col, (title, data) in enumerate((("Фіксовані R = 5 реплік", fixed), ("HPA", hpa))):
        xs = [x["s"] for x in data]
        axes[0][col].plot(xs, [x["z"] for x in data], label="задана")
        axes[0][col].plot(xs, [x["achieved"] for x in data], label="досягнута")
        axes[0][col].set_title(title)
        axes[1][col].plot(xs, [x["p99"] if x["p99"] is not None else math.nan for x in data])
        axes[1][col].set_yscale("log")
        axes[2][col].step(xs, [x["d"] for x in data], where="post", label="d(t)")
        axes[2][col].step(xs, [x["s_ready"] for x in data], where="post", label="s(t)")
        axes[3][col].plot(xs, [x["cpu_rel"] * 100 if x["cpu_rel"] is not None else math.nan for x in data])
        axes[3][col].axhline(70, linestyle="--", color="gray")
        axes[3][col].set_xlabel("час від початку прогону, с")
        axes[0][col].legend()
        axes[2][col].legend()
    axes[0][0].set_ylabel("запитів/с")
    axes[1][0].set_ylabel("p99 клієнта, мс")
    axes[2][0].set_ylabel("реплік")
    axes[3][0].set_ylabel("CPU / requests, %")
    fig.tight_layout()
    fig.savefig(os.path.join(CHARTS, "elastic.png"), dpi=110)
    plt.close(fig)


def main():
    os.makedirs(CHARTS, exist_ok=True)
    out = {"runs": {}}
    for n in NS:
        summary, _ = load(f"step_n{n}")
        best = next(s for s in summary["steps"] if s["step"] == summary["X_step"])
        t = best["end_unix"]
        thr = throttling(t)
        pod_rps = by_label(f"sum by (pod) (rate(http_requests_total{{{READ}}}[20s]))", t, "pod")
        node_rps = by_label(f"sum by (node) (rate(http_requests_total{{{READ}}}[20s]))", t, "node")
        run = {"X": summary["X"], "X_std": summary["X_std"], "X_step": summary["X_step"], "X_target": summary["X_target_rps"],
               "not_reached": summary["last_step_achieved_ratio"] >= 0.9, "best": best,
               "server_p99": server_p99(t), "throttling": thr,
               "throttling_avg": sum(thr.values()) / len(thr) if thr else None,
               "throttling_max": max(thr.values()) if thr else None,
               "pod_rps": pod_rps, "node_rps": node_rps,
               "pods": open(os.path.join(R, f"pods_n{n}.txt")).read().split("\n")}
        if n > 1 and pod_rps:
            avg = sum(pod_rps.values()) / len(pod_rps)
            hi, lo = max(pod_rps, key=pod_rps.get), min(pod_rps, key=pod_rps.get)
            pod_p99 = server_p99(t, by="pod")
            run["imbalance"] = {"I": pod_rps[hi] / avg, "X_max": pod_rps[hi], "X_avg": avg,
                                "max_pod": {"pod": hi, "rps": pod_rps[hi], "p99": pod_p99.get(hi), "throttling": thr.get(hi)},
                                "min_pod": {"pod": lo, "rps": pod_rps[lo], "p99": pod_p99.get(lo), "throttling": thr.get(lo)}}
        if n == RMAX:
            start, end = summary["t0_unix"], summary["steps"][-1]["end_unix"]
            fig, ax = plt.subplots(figsize=(11, 4.5))
            for r in series(f"sum by (pod) (rate(http_requests_total{{{READ}}}[20s]))", start, end, step=5):
                ax.plot([float(tt) - start for tt, _ in r["values"]], [float(v) for _, v in r["values"]], label=r["metric"]["pod"])
            ax.set_xlabel("час від початку прогону, с")
            ax.set_ylabel("запитів/с")
            ax.set_title(f"Запити за секунду кожного пода, {RMAX} реплік")
            ax.legend(fontsize=8)
            fig.tight_layout()
            fig.savefig(os.path.join(CHARTS, "pods_rps_n5.png"), dpi=110)
            plt.close(fig)
        out["runs"][n] = run
    X1 = out["runs"][1]["X"]
    points = [(n, out["runs"][n]["X"] / X1) for n in NS if n > 1 and not out["runs"][n]["not_reached"]]
    fit = fit_usl(points)
    out["usl"] = fit
    X = {n: out["runs"][n]["X"] if n in out["runs"] else usl(n, X1, fit["sigma"], fit["kappa"]) for n in range(1, RMAX + 1)}
    out["X_for_d"] = X
    lab2 = json.load(open(LAB2))
    fig, ax = plt.subplots(figsize=(8, 5))
    ns = [n / 10 for n in range(10, 61)]
    l2x1 = lab2["runs"]["1"]["X"]
    ax.errorbar([1, 2, 3, 4], [lab2["runs"][str(n)]["X"] / l2x1 for n in (1, 2, 3, 4)],
                yerr=[lab2["runs"][str(n)]["X_std"] / l2x1 for n in (1, 2, 3, 4)], fmt="o", capsize=4, label="Compose, ЛР2")
    ax.plot(ns, [usl(n, 1, lab2["usl"]["sigma"], lab2["usl"]["kappa"]) for n in ns], label=f"Compose: σ = {lab2['usl']['sigma']:.3f}, κ = {lab2['usl']['kappa']:.4f}")
    ax.errorbar(list(NS), [out["runs"][n]["X"] / X1 for n in NS], yerr=[out["runs"][n]["X_std"] / X1 for n in NS],
                fmt="s", capsize=4, label="Kubernetes")
    ax.plot(ns, [usl(n, 1, fit["sigma"], fit["kappa"]) for n in ns], label=f"Kubernetes: σ = {fit['sigma']:.3f}, κ = {fit['kappa']:.4f}")
    ax.set_xlabel("кількість екземплярів N")
    ax.set_ylabel("C(N) = X(N) / X(1)")
    ax.legend()
    fig.tight_layout()
    fig.savefig(os.path.join(CHARTS, "usl.png"), dpi=110)
    plt.close(fig)
    fixed_summary, _ = load("fixed")
    p99_0 = fixed_summary["segments"][0]["p99_ms"]
    spike_p99 = fixed_summary["segments"][1]["p99_ms"]
    fixed, fm, fsegs = elastic("fixed", X, p99_0, spike_p99)
    hpa, hm, hsegs = elastic("hpa", X, p99_0, spike_p99)
    out.update({"p99_0": p99_0, "spike_p99_fixed": spike_p99, "fixed": fm, "hpa": hm,
                "fixed_segments": fsegs, "hpa_segments": hsegs})
    chart_elastic(fixed, hpa)
    with open(os.path.join(R, "analysis.json"), "w") as f:
        json.dump(out, f, indent=2, ensure_ascii=False, default=str)
    for n in NS:
        r = out["runs"][n]
        print(n, r["X"], r["X_std"], r["X_target"], r["not_reached"], r["best"]["p99_ms"], r["server_p99"],
              r["throttling_avg"], r["throttling_max"], r.get("imbalance", {}).get("I"), r["node_rps"])
    print(json.dumps(fit, default=str))
    print("fixed", json.dumps(fm))
    print("hpa", json.dumps(hm))


if __name__ == "__main__":
    main()
