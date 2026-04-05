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


