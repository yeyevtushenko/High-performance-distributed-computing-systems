import glob
import json
import re
import statistics
from collections import defaultdict

FIELDS = ["achieved_rps", "p50_ms", "p95_ms", "p99_ms", "non_2xx_share", "mean_in_flight", "mean_ms"]


def median_of(runs, field):
    values = [r[field] for r in runs if r[field] is not None]
    return statistics.median(values) if values else None


groups = defaultdict(list)
for path in glob.glob("results/open_n*_r*_*.json"):
    n, r, _ = map(int, re.findall(r"open_n(\d+)_r(\d+)_(\d+)", path)[0])
    groups[(n, r)].append(json.load(open(path)))

rows = ["| Інстанси | Задана, запитів/с | Досягнута, запитів/с | p50, мс | p95, мс | p99, мс | Не 2xx | L | W, мс |",
        "|---|---|---|---|---|---|---|---|---|"]
for (n, r) in sorted(groups):
    m = {f: median_of(groups[(n, r)], f) for f in FIELDS}
    rows.append(
        f"| {n} | {r} | {m['achieved_rps']:.1f} | {m['p50_ms']:.2f} | {m['p95_ms']:.2f} | {m['p99_ms']:.2f} "
        f"| {m['non_2xx_share'] * 100:.2f} % | {m['mean_in_flight']:.2f} | {m['mean_ms']:.2f} |"
    )
print("\n".join(rows))

print("\nNoop:")
for path in sorted(glob.glob("results/noop_r*.json"), key=lambda p: int(re.findall(r"\d+", p)[0])):
    d = json.load(open(path))
    print(f"{d['target_rps']} -> {d['achieved_rps']} rps, p50 {d['p50_ms']} ms, p99 {d['p99_ms']} ms, non-2xx {d['non_2xx_share']}")

print("\nClosed:")
for path in glob.glob("results/closed_*.json"):
    d = json.load(open(path))
    print(f"concurrency {d['concurrency']}: {d['achieved_rps']} rps, p50 {d['p50_ms']} ms, p95 {d['p95_ms']} ms, p99 {d['p99_ms']} ms")
