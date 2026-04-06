| Option | Hardware | p50 | p95 | Throughput (req/s) | Error rate |
|---|---|---|---|---|---|
| baseline_pytorch | compute_skylake | - | - | - | - |
| onnx_baseline | compute_skylake | 7ms | 14ms | 10.00 | 0.0% |
| onnx_graph_opt | compute_skylake | 7ms | 14ms | 9.96 | 0.0% |
| onnx_dynamic_int8 | compute_skylake | 7ms | 15ms | 9.86 | 0.0% |
| onnx_static_int8_agg | compute_skylake | 7ms | 14ms | 9.95 | 0.0% |
| onnx_static_int8_cons | compute_skylake | 7ms | 15ms | 9.81 | 0.0% |
| fastapi_sequential | compute_skylake | 7ms | 14ms | 9.95 | 0.0% |
| fastapi_concurrent | compute_skylake | 7ms | 13ms | 9.99 | 0.0% |

## Right-sizing note (fastapi_concurrent)
- Instance: m1.medium (compute_skylake)
- vCPUs: 2
- RAM: 3.8GB total, ~910MB used under representative load
- No GPU, no swap



## Notebook 6 - Sequential vs Concurrent (FastAPI direct benchmark)

| Test | p50 | p95 | p99 | Throughput | Errors |
|------|-----|-----|-----|------------|--------|
| Sequential (100 requests, 1 worker) | 3.49ms | 6.94ms | 8.35ms | 242.88 req/sec | 0 |
| Concurrent (1000 requests, 16 workers) | 42.06ms | 73.25ms | 96.52ms | 321.07 req/sec | 0 |
