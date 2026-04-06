import csv, os

OPTIONS = [
    "baseline_pytorch",
    "onnx_baseline",
    "onnx_graph_opt",
    "onnx_dynamic_int8",
    "onnx_static_int8_agg",
    "onnx_static_int8_cons",
    "fastapi_sequential",
    "fastapi_concurrent",
]


def discover_lightgbm_options():
    results_dir = "results"
    if not os.path.isdir(results_dir):
        return []
    return sorted(
        name for name in os.listdir(results_dir)
        if name.startswith("lightgbm") and os.path.isdir(os.path.join(results_dir, name))
    )


def discover_rayserve_options():
    results_dir = "results"
    if not os.path.isdir(results_dir):
        return []
    return sorted(
        name for name in os.listdir(results_dir)
        if name.startswith("rayserve") and os.path.isdir(os.path.join(results_dir, name))
    )


def read_stats(option, prefix):
    path = f"results/{option}/{prefix}_stats.csv"
    if not os.path.exists(path):
        return {"p50": "-", "p95": "-", "rps": "-", "err": "-"}
    with open(path) as f:
        for row in csv.DictReader(f):
            if row.get("Name") == "Aggregated":
                total = max(float(row.get("Request Count", 1)), 1)
                return {
                    "p50": f"{float(row.get('50%', 0)):.0f}ms",
                    "p95": f"{float(row.get('95%', 0)):.0f}ms",
                    "rps": f"{float(row.get('Requests/s', 0)):.2f}",
                    "err": f"{float(row.get('Failure Count', 0)) / total * 100:.1f}%",
                }
    return {"p50": "-", "p95": "-", "rps": "-", "err": "-"}


rows = []
for opt in OPTIONS + discover_lightgbm_options() + discover_rayserve_options():
    peak = read_stats(opt, "peak")
    rows.append(f"| {opt} | compute_skylake | {peak['p50']} | {peak['p95']} | {peak['rps']} | {peak['err']} |")

print("| Option | Hardware | p50 | p95 | Throughput (req/s) | Error rate |")
print("|---|---|---|---|---|---|")
for r in rows:
    print(r)
