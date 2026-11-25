import argparse
import json
import os
import platform
import statistics
import subprocess
import sys
import time

import numpy as np
import torch
import torch.distributed as dist
from sklearn.metrics import roc_auc_score
from torch import nn
from torch.nn.parallel import DistributedDataParallel as DDP
from torch.utils.data import DataLoader, DistributedSampler, TensorDataset

DATASET, K, C = "bloodmnist", 8, 3
C1, C2, H = 64, 128, 512
P_MAX = 2
N_TRAIN = 7000
LR0 = 0.02
MOMENTUM = 0.9
EPOCHS = 3
WARMUP_STEPS = 10
MEASURE_STEPS = 50
C_PHYS = 11
N_TH = C_PHYS // P_MAX
DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", f"{DATASET}.npz")


def load_split(path, split, n=None):
    d = np.load(path)
    x = d[f"{split}_images"][:n]
    y = d[f"{split}_labels"][:n].reshape(-1)
    if x.ndim == 3:
        x = x[:, None, :, :]
    else:
        x = x.transpose(0, 3, 1, 2)
    x = torch.from_numpy(np.ascontiguousarray(x)).float()
    x = (x / 255 - 0.5) / 0.5
    return TensorDataset(x, torch.from_numpy(y.astype(np.int64)))


class Net(nn.Module):
    def __init__(self, C, c1, c2, h, K):
        super().__init__()
        self.features = nn.Sequential(
            nn.Conv2d(C, c1, 3, padding=1), nn.ReLU(), nn.MaxPool2d(2),
            nn.Conv2d(c1, c2, 3, padding=1), nn.ReLU(), nn.MaxPool2d(2),
        )
        self.head = nn.Sequential(
            nn.Flatten(), nn.Linear(7 * 7 * c2, h), nn.ReLU(),
            nn.Linear(h, K),
        )

    def forward(self, x):
        return self.head(self.features(x))


def n_params_formula(C, c1, c2, h, K):
    return 9 * C * c1 + c1 + 9 * c1 * c2 + c2 + 49 * c2 * h + h + h * K + K


class Ctx:
    def __init__(self):
        self.distributed = "RANK" in os.environ
        if self.distributed:
            dist.init_process_group(backend="gloo")
            self.rank, self.world = dist.get_rank(), dist.get_world_size()
        else:
            self.rank, self.world = 0, 1

    def log(self, *a):
        if self.rank == 0:
            print(*a, flush=True)


def build(ctx, sync):
    torch.manual_seed(0)
    model = Net(C, C1, C2, H, K)
    if ctx.distributed and sync == "manual":
        for p in model.parameters():
            dist.broadcast(p.data, src=0)
    if ctx.distributed and sync == "ddp":
        return model, DDP(model)
    return model, model


def loader(ctx, dataset, batch, epoch, seed=0):
    if ctx.distributed:
        sampler = DistributedSampler(dataset, num_replicas=ctx.world, rank=ctx.rank, shuffle=True, seed=seed)
    else:
        sampler = DistributedSampler(dataset, num_replicas=1, rank=0, shuffle=True, seed=seed)
    sampler.set_epoch(epoch)
    return DataLoader(dataset, batch_size=batch, sampler=sampler, drop_last=True)


def manual_sync(ctx, model, times=None):
    start = time.perf_counter()
    for p in model.parameters():
        dist.all_reduce(p.grad, op=dist.ReduceOp.SUM)
        p.grad /= ctx.world
    if times is not None:
        times.append(time.perf_counter() - start)


def train_step(ctx, model, wrapped, opt, x, y, sync, no_sync=False, sync_times=None):
    opt.zero_grad()
    loss = nn.functional.cross_entropy(wrapped(x), y)
    if sync == "ddp" and no_sync:
        with wrapped.no_sync():
            loss.backward()
    else:
        loss.backward()
    if ctx.distributed and sync == "manual":
        manual_sync(ctx, model, sync_times)
    opt.step()
    return loss.detach()


def batches(ctx, dataset, b, start_epoch=0, skip=0):
    epoch = start_epoch
    while True:
        for i, (x, y) in enumerate(loader(ctx, dataset, b, epoch)):
            if epoch == start_epoch and i < skip:
                continue
            yield epoch, i, x, y
        epoch += 1


def evaluate(model, test):
    model.eval()
    with torch.no_grad():
        logits = torch.cat([model(x) for x, _ in DataLoader(test, batch_size=1024)])
    model.train()
    prob = torch.softmax(logits, dim=1).numpy()
    y = test.tensors[1].numpy()
    return {"ACC": float((prob.argmax(1) == y).mean()),
            "AUC": float(roc_auc_score(y, prob, multi_class="ovr", average="macro"))}


def save(path, data):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print(json.dumps({k: v for k, v in data.items() if k not in ("losses", "step_times_ms")}, ensure_ascii=False), flush=True)


def speed(ctx, args, train):
    model, wrapped = build(ctx, args.sync)
    opt = torch.optim.SGD(model.parameters(), lr=args.lr, momentum=MOMENTUM)
    b = args.batch // ctx.world
    it = batches(ctx, train, b)
    times, syncs = [], []
    t_begin = None
    for k in range(WARMUP_STEPS + MEASURE_STEPS):
        _, _, x, y = next(it)
        measured = k >= WARMUP_STEPS
        if measured and t_begin is None:
            t_begin = time.perf_counter()
        start = time.perf_counter()
        train_step(ctx, model, wrapped, opt, x, y, args.sync, args.no_sync, syncs if measured else None)
        times.append(time.perf_counter() - start)
    t = time.perf_counter() - t_begin
    times = times[WARMUP_STEPS:]
    result = {"P": ctx.world, "b": b, "B": args.batch, "sync": args.sync if ctx.distributed else "none",
              "no_sync": args.no_sync, "n_th": torch.get_num_threads(),
              "t_step_ms": statistics.median(times) * 1000, "t_50_s": t, "X": MEASURE_STEPS * args.batch / t,
              "step_times_ms": [v * 1000 for v in times]}
    if syncs:
        result["t_sync_ms"] = statistics.median(syncs) * 1000
    if ctx.rank == 0:
        save(args.out, result)


def check(ctx, args, train):
    model, _ = build(ctx, "manual")
    x, y = train.tensors[0][:args.batch], train.tensors[1][:args.batch]
    ratio = None
    if ctx.rank == 0:
        ref = Net(C, C1, C2, H, K)
        ref.load_state_dict(model.state_dict())
        nn.functional.cross_entropy(ref(x), y).backward()
        g = [p.grad.clone() for p in ref.parameters()]
    model.zero_grad()
    nn.functional.cross_entropy(model(x[ctx.rank::ctx.world]), y[ctx.rank::ctx.world]).backward()
    manual_sync(ctx, model)
    if ctx.rank == 0:
        ratios = [((gi - p.grad).abs().max() / gi.abs().max()).item() for gi, p in zip(g, model.parameters())]
        ratio = max(ratios)
    opt = torch.optim.SGD(model.parameters(), lr=args.lr, momentum=MOMENTUM)
    it = batches(ctx, train, args.batch // ctx.world)
    for _ in range(50):
        _, _, xb, yb = next(it)
        train_step(ctx, model, model, opt, xb, yb, "manual")
    total = torch.tensor([sum(p.detach().double().sum().item() for p in model.parameters())], dtype=torch.float64)
    sums = [torch.zeros(1, dtype=torch.float64) for _ in range(ctx.world)]
    dist.all_gather(sums, total)
    if ctx.rank == 0:
        values = [s.item() for s in sums]
        save(args.out, {"P": ctx.world, "B": args.batch, "max_grad_ratio": ratio, "per_tensor_ratio": ratios,
                        "weight_sums": values, "weight_sum_diff": max(values) - min(values)})


def checkpoint(ctx, model, opt, epoch, i, step, path):
    if ctx.rank == 0:
        state = {"model": model.state_dict(), "opt": opt.state_dict(), "epoch": epoch,
                 "step_in_epoch": i + 1, "step": step}
        torch.save(state, path + ".tmp")
        os.replace(path + ".tmp", path)
        event(ctx, args_global.events, {"event": "checkpoint", "step": step, "t": time.time()})
    if ctx.distributed:
        dist.barrier()


def event(ctx, path, data):
    if path:
        with open(path, "a") as f:
            f.write(json.dumps(data) + "\n")


def train_run(ctx, args, train, test):
    model, wrapped = build(ctx, args.sync)
    opt = torch.optim.SGD(model.parameters(), lr=args.lr, momentum=MOMENTUM)
    b = args.batch // ctx.world
    start_epoch, skip, step = 0, 0, 0
    if args.ckpt and os.path.exists(args.ckpt):
        state = torch.load(args.ckpt)
        model.load_state_dict(state["model"])
        opt.load_state_dict(state["opt"])
        start_epoch, skip, step = state["epoch"], state["step_in_epoch"], state["step"]
        event(ctx, args.events if ctx.rank == 0 else None, {"event": "restored", "step": step, "t": time.time()})
    steps_per_epoch = len(loader(ctx, train, b, 0))
    total_steps = EPOCHS * steps_per_epoch
    losses = []
    first_after_restore = step > 0
    for epoch, i, x, y in batches(ctx, train, b, start_epoch, skip):
        if step >= total_steps:
            break
        if args.fail_step and step + 1 == args.fail_step and ctx.rank == ctx.world - 1 and not os.path.exists(args.marker):
            open(args.marker, "w").close()
            event(ctx, args.events, {"event": "failure", "step": step + 1, "rank": ctx.rank, "t": time.time()})
            os._exit(1)
        loss = train_step(ctx, model, wrapped, opt, x, y, args.sync)
        if ctx.distributed:
            dist.all_reduce(loss, op=dist.ReduceOp.SUM)
            loss /= ctx.world
        step += 1
        losses.append({"step": step, "epoch": epoch, "loss": loss.item()})
        if ctx.rank == 0 and args.events:
            event(ctx, args.events, {"event": "step", "step": step, "epoch": epoch, "loss": loss.item(), "t": time.time()})
        if first_after_restore:
            event(ctx, args.events if ctx.rank == 0 else None, {"event": "first_step_after_restore", "step": step, "t": time.time()})
            first_after_restore = False
        if args.ckpt_every and step % args.ckpt_every == 0:
            checkpoint(ctx, model, opt, epoch, i, step, args.ckpt)
    if ctx.rank == 0:
        last = [r["loss"] for r in losses if r["epoch"] == EPOCHS - 1]
        save(args.out, {"P": ctx.world, "b": b, "B": args.batch, "lr": args.lr, "sync": args.sync if ctx.distributed else "none",
                        "epochs": EPOCHS, "steps_per_epoch": steps_per_epoch, "optimizer_steps": step,
                        "max_loss_last_epoch": max(last) if last else None, **evaluate(model, test), "losses": losses})


def info(args):
    model = Net(C, C1, C2, H, K)
    n = sum(p.numel() for p in model.parameters())
    assert n == n_params_formula(C, C1, C2, H, K)
    d = np.load(DATA)
    sysctl = lambda key: subprocess.run(["sysctl", "-n", key], capture_output=True, text=True).stdout.strip()
    save(args.out, {"cpu": sysctl("machdep.cpu.brand_string"), "C_phys": int(sysctl("hw.physicalcpu")),
                    "logical": os.cpu_count(), "memory_GB": int(sysctl("hw.memsize")) / 2**30,
                    "os": platform.platform(), "python": sys.version.split()[0], "torch": torch.__version__,
                    "n_th": N_TH, "dataset": DATASET, "K": K, "C": C, "c1_c2_h": [C1, C2, H], "n_params": n,
                    "M_MiB": 4 * n / 2**20, "param_tensors": len(list(model.parameters())), "N": N_TRAIN,
                    "train_total": len(d["train_labels"]), "test": len(d["test_labels"]), "B": 192, "P_max": P_MAX})


args_global = None


def main():
    global args_global
    p = argparse.ArgumentParser()
    p.add_argument("mode", choices=["info", "speed", "check", "train"])
    p.add_argument("--sync", choices=["manual", "ddp"], default="ddp")
    p.add_argument("--batch", type=int, default=192)
    p.add_argument("--lr", type=float, default=LR0)
    p.add_argument("--no-sync", action="store_true")
    p.add_argument("--ckpt")
    p.add_argument("--ckpt-every", type=int)
    p.add_argument("--fail-step", type=int)
    p.add_argument("--marker", default="results/fail.marker")
    p.add_argument("--events")
    p.add_argument("--out", required=True)
    args = args_global = p.parse_args()
    torch.set_num_threads(N_TH)
    if args.mode == "info":
        return info(args)
    ctx = Ctx()
    train = load_split(DATA, "train", n=N_TRAIN)
    test = load_split(DATA, "test")
    {"speed": lambda: speed(ctx, args, train), "check": lambda: check(ctx, args, train),
     "train": lambda: train_run(ctx, args, train, test)}[args.mode]()
    if ctx.distributed:
        dist.destroy_process_group()


if __name__ == "__main__":
    main()
