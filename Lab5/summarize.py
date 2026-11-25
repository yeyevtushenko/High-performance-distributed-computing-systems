import json
import statistics

RUNS = (1, 2, 3)
FIELDS = ("ops", "X", "x_per_thread", "p50_us", "p99_us", "h", "max_wait_ms", "w", "cv")


def load(name):
    with open(f"results/{name}.json") as f:
        return json.load(f)


def med(values):
    values = [v for v in values if v is not None]
    return statistics.median(values) if values else None


def summary(sync):
    runs = [load(f"run_{sync}_{i}") for i in RUNS]
    out = {role: {k: med([r[role][k] for r in runs]) for k in FIELDS} for role in ("write", "read")}
    out["X"] = med([r["X"] for r in runs])
    out["ops"] = med([r["write"]["ops"] + r["read"]["ops"] for r in runs])
    out["phi_w"] = med([r["phi_w"] for r in runs])
    out["phi_r"] = med([r["phi_r"] for r in runs])
    out["per_thread"] = {tid: med([r["per_thread"][tid]["ops"] for r in runs]) for tid in runs[0]["per_thread"]}
    out["roles"] = {tid: runs[0]["per_thread"][tid]["role"] for tid in runs[0]["per_thread"]}
    out["invariants"] = [r["invariants"] for r in runs]
    out["X_1"] = med([load(f"x1_{sync}_{i}")["X_1"] for i in RUNS])
    out["runs_X"] = [r["X"] for r in runs]
    return out


def ratio(rw, g):
    if rw is None or g is None:
        return None
    return rw / g if g else rw - g


def main():
    g, rw = summary("global"), summary("rw")
    tcs = [load(f"tcs_{i}") for i in RUNS]
    t_w = med([t["t_cs_w_us"] for t in tcs])
    t_r = med([t["t_cs_r_us"] for t in tcs])
    t_bar = med([t["t_cs_bar_us"] for t in tcs])
    limit = 1e6 / t_bar
    metrics = {
        "S_G": g["X"] / g["X_1"], "E_G": g["X"] / g["X_1"] / 12,
        "S_RW": rw["X"] / rw["X_1"], "E_RW": rw["X"] / rw["X_1"] / 12,
        "S_w": rw["write"]["X"] / g["write"]["X"], "S_r": rw["read"]["X"] / g["read"]["X"],
        "S_sum": rw["X"] / g["X"], "rho": g["X"] * t_bar / 1e6,
    }
    compare = {}
    for k in FIELDS[1:]:
        compare[k] = {"G_w": g["write"][k], "G_r": g["read"][k], "RW_w": rw["write"][k], "RW_r": rw["read"][k],
                      "ratio_w": ratio(rw["write"][k], g["write"][k]), "ratio_r": ratio(rw["read"][k], g["read"][k])}
    compare["phi"] = {"G_w": g["phi_w"], "G_r": g["phi_r"], "RW_w": rw["phi_w"], "RW_r": rw["phi_r"],
                      "ratio_w": ratio(rw["phi_w"], g["phi_w"]), "ratio_r": ratio(rw["phi_r"], g["phi_r"])}
    out = {"info": load("info"), "global": g, "rw": rw,
           "t_cs_w_us": t_w, "t_cs_r_us": t_r, "t_cs_bar_us": t_bar, "limit_ops": limit,
           "metrics": metrics, "compare": compare, "readonly": load("readonly")}
    with open("results/summary.json", "w") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)
    print(json.dumps({k: out[k] for k in ("t_cs_w_us", "t_cs_r_us", "t_cs_bar_us", "limit_ops", "metrics")}, indent=2))
    print("X_1", g["X_1"], rw["X_1"], "X", g["runs_X"], rw["runs_X"])


if __name__ == "__main__":
    main()
