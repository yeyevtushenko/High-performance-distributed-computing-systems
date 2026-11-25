import json
import math
import os

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

RESULTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "results")


def load(name):
    with open(os.path.join(RESULTS, name)) as f:
        return json.load(f)


def usl():
    data = load("step6/summary.json")
    ws = [v["W"] for v in data.values()]
    xs = [v["X"] for v in data.values()]
    stds = [v["X_std_per_second"] for v in data.values()]
    x1 = xs[0]
    pts = [(w, x / x1) for w, x in zip(ws, xs) if w > 1]
    u1 = [w - 1 for w, _ in pts]
    u2 = [w * (w - 1) for w, _ in pts]
    y = [w / c - 1 for w, c in pts]
    a11 = sum(a * a for a in u1)
    a12 = sum(a * b for a, b in zip(u1, u2))
    a22 = sum(b * b for b in u2)
    b1 = sum(a * c for a, c in zip(u1, y))
    b2 = sum(b * c for b, c in zip(u2, y))
    det = a11 * a22 - a12 * a12
    sigma_raw = (b1 * a22 - b2 * a12) / det
    kappa_raw = (a11 * b2 - a12 * b1) / det
    sigma, kappa = sigma_raw, kappa_raw
    if kappa < 0:
        kappa = 0.0
        sigma = b1 / a11
    if sigma < 0:
        sigma = 0.0
        kappa = b2 / a22 if kappa_raw >= 0 else 0.0
    w_star = math.sqrt((1 - sigma) / kappa) if kappa > 0 else None
    fit = {"X_1": x1, "C": {w: x / x1 for w, x in zip(ws, xs)}, "sigma_raw": sigma_raw, "kappa_raw": kappa_raw,
           "sigma": sigma, "kappa": kappa, "W_star": w_star,
           "limit_if_kappa_0": (1 / sigma) if sigma > 0 and kappa == 0 else None}
    with open(os.path.join(RESULTS, "step6", "usl.json"), "w") as f:
        json.dump(fit, f, indent=2)
    print(json.dumps(fit))
    grid = [1 + i * 0.05 for i in range(int((max(ws) + 1 - 1) / 0.05) + 1)]
    curve = [x1 * w / (1 + sigma * (w - 1) + kappa * w * (w - 1)) for w in grid]
    plt.figure(figsize=(6.4, 4.2))
    plt.errorbar(ws, xs, yerr=stds, fmt="o", capsize=4, label="X(W), розкид за секундами")
    plt.plot(grid, curve, label=f"USL: σ={sigma:.3f}, κ={kappa:.4f}")
    plt.plot([1, max(ws) + 1], [x1, x1 * (max(ws) + 1)], "--", color="grey", label="W · X(1)")
    plt.xlabel("W, воркерів")
    plt.ylabel("X, задач/с")
    plt.legend()
    plt.grid(alpha=0.3)
    plt.tight_layout()
    plt.savefig(os.path.join(RESULTS, "step6", "x_w.png"), dpi=130)


def timeline():
    data = load("step9/timeline.json")
    t0 = data["t0"]
    fig, (top, bottom) = plt.subplots(2, 1, figsize=(8, 6), sharex=True)
    series = {name: s[0]["values"] for name, s in data["prom"].items() if s}
    if "tasks_queued" in series:
        top.plot([float(t) - t0 for t, _ in series["tasks_queued"]], [float(v) for _, v in series["tasks_queued"]],
                 label="tasks_queued")
    finished = data["finished"]
    if finished:
        start = int(min(finished))
        counts = [0] * (int(max(finished)) - start + 1)
        for t in finished:
            counts[int(t) - start] += 1
        bottom.plot([start + i - t0 for i in range(len(counts))], counts, linewidth=0.6, color="tab:green",
                    label="завершено задач за секунду")
    if "tasks_running" in series:
        bottom.plot([float(t) - t0 for t, _ in series["tasks_running"]], [float(v) for _, v in series["tasks_running"]],
                    color="tab:orange", label="tasks_running")
    for ax in (top, bottom):
        ax.axvline(data["scenario_at"] - t0, color="red", linestyle="--", label="Ф2: pg_terminate_backend")
        ax.axvline(data["t_gen_end"] - t0, color="grey", linestyle=":", label="кінець генерації")
        ax.grid(alpha=0.3)
        ax.legend(fontsize=8, loc="upper right")
    top.set_ylabel("задач у черзі")
    bottom.set_ylabel("задач")
    bottom.set_xlabel("час від початку прогону, с")
    fig.tight_layout()
    fig.savefig(os.path.join(RESULTS, "step9", "timeline.png"), dpi=130)


if __name__ == "__main__":
    usl()
    timeline()
