import json
import os

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

R = "results"
P_MAX = 2
S_F = 37


def load(name):
    with open(os.path.join(R, name)) as f:
        return json.load(f)


def main():
    one, one_train = load("step1_speed.json"), load("step1_train.json")
    manual, ddp, ddp_ns = load("step2_manual.json"), load("step2_ddp.json"), load("step2_ddp_nosync.json")
    check, ddp_train = load("step2_check.json"), load("step2_ddp_train.json")
    t_comm = ddp["t_step_ms"] - ddp_ns["t_step_ms"]
    table2 = {
        "one": {"t_step_ms": one["t_step_ms"], "comm_ms": 0, "c": 0, "X": one["X"]},
        "manual": {"t_step_ms": manual["t_step_ms"], "comm_ms": manual["t_sync_ms"],
                   "c": manual["t_sync_ms"] / manual["t_step_ms"], "X": manual["X"]},
        "ddp": {"t_step_ms": ddp["t_step_ms"], "comm_ms": t_comm, "c": t_comm / ddp["t_step_ms"], "X": ddp["X"]},
        "ddp_no_sync": {"t_step_ms": ddp_ns["t_step_ms"], "comm_ms": 0, "c": 0, "X": ddp_ns["X"]},
        "max_grad_ratio": check["max_grad_ratio"], "weight_sum_diff": check["weight_sum_diff"],
        "ACC_one": one_train["ACC"], "ACC_ddp": ddp_train["ACC"], "AUC_one": one_train["AUC"], "AUC_ddp": ddp_train["AUC"],
    }
    p1, p1_ns = load("step3_p1.json"), load("step3_p1_nosync.json")
    rows = {1: (p1, p1_ns), 2: (ddp, ddp_ns)}
    x1 = p1["X"]
    table3 = {}
    for p, (sp, ns) in rows.items():
        s = sp["X"] / x1
        table3[p] = {"b": sp["b"], "t_step_ms": sp["t_step_ms"], "t_step_ns_ms": ns["t_step_ms"], "X": sp["X"],
                     "S": s, "E": s / p, "e": None if p == 1 else (1 / s - 1 / p) / (1 - 1 / p),
                     "c": (sp["t_step_ms"] - ns["t_step_ms"]) / sp["t_step_ms"]}
    w1 = load("step4_p1.json")
    table4 = {1: {"B_P": w1["B"], "t_step_ms": w1["t_step_ms"], "X": w1["X"], "S_w": 1.0, "E_w": 1.0},
              2: {"B_P": ddp["B"], "t_step_ms": ddp["t_step_ms"], "X": ddp["X"],
                  "S_w": ddp["X"] / w1["X"], "E_w": ddp["X"] / w1["X"] / 2}}
    table5 = {}
    for name, f in (("P1_b", "step4_p1_train.json"), ("Pmax_lr0", "step2_ddp_train.json"),
                    ("Pmax_lrP", "step4_p2_lr_scaled_train.json")):
        d = load(f)
        table5[name] = {"B_P": d["B"], "lr": d["lr"], "steps": d["optimizer_steps"], "ACC": d["ACC"], "AUC": d["AUC"],
                        "max_loss_last_epoch": d["max_loss_last_epoch"]}
    events = [json.loads(line) for line in open(os.path.join(R, "step5_events.jsonl"))]
    failure = next(e for e in events if e["event"] == "failure")
    ckpts = [e for e in events if e["event"] == "checkpoint" and e["t"] < failure["t"]]
    last_ckpt = ckpts[-1]
    restored = next(e for e in events if e["event"] == "restored")
    first = next(e for e in events if e["event"] == "first_step_after_restore")
    after = {e["step"]: e["loss"] for e in events if e["event"] == "step" and e["t"] > failure["t"]}
    before = {e["step"]: e["loss"] for e in events if e["event"] == "step" and e["t"] < failure["t"]}
    base = {r["step"]: r["loss"] for r in ddp_train["losses"]}
    fail_train = load("step5_train.json")
    steps = list(range(S_F - 2, S_F + 3))
    table6 = {"K_c": 15, "s_f": S_F, "last_ckpt_step": last_ckpt["step"], "restored_step": restored["step"],
              "repeated_steps": failure["step"] - 1 - last_ckpt["step"],
              "t_f_minus_t_c_s": failure["t"] - last_ckpt["t"], "t_r_s": first["t"] - failure["t"],
              "t_lost_s": failure["t"] - last_ckpt["t"] + first["t"] - failure["t"],
              "loss_fail": {s: after.get(s, before.get(s)) for s in steps},
              "loss_fail_before_failure": {s: before.get(s) for s in steps if s in before},
              "loss_no_fail": {s: base[s] for s in steps},
              "max_abs_diff_all_steps": max(abs(after.get(s, before.get(s)) - base[s]) for s in base),
              "ACC_fail": fail_train["ACC"], "ACC_no_fail": ddp_train["ACC"],
              "AUC_fail": fail_train["AUC"], "AUC_no_fail": ddp_train["AUC"],
              "optimizer_steps_fail": fail_train["optimizer_steps"]}
    out = {"table2": table2, "table3": table3, "table4": table4, "table5": table5, "table6": table6}
    with open(os.path.join(R, "summary.json"), "w") as f:
        json.dump(out, f, indent=2)
    print(json.dumps(out, indent=2))
    ps = sorted(table3)
    plt.figure(figsize=(5.5, 4))
    plt.plot(ps, [table3[p]["X"] for p in ps], "o-", label="X(P), DDP")
    plt.plot(ps, [p * x1 for p in ps], "--", color="grey", label="P · X(1)")
    plt.xticks(ps)
    plt.xlabel("P, процесів")
    plt.ylabel("X, прикладів/с")
    plt.legend()
    plt.grid(alpha=0.3)
    plt.tight_layout()
    plt.savefig(os.path.join(R, "strong_scaling.png"), dpi=130)


if __name__ == "__main__":
    main()
