| Option | Hardware | p50 | p95 | Throughput (req/s) | Error rate |
|---|---|---|---|---|---|
| baseline_pytorch | compute_skylake | - | - | - | - |
| onnx_baseline | compute_skylake | 7ms | 14ms | 10.00 | 0.0% |
| onnx_graph_opt | compute_skylake | 7ms | 14ms | 9.96 | 0.0% |
| onnx_dynamic_int8 | compute_skylake | 7ms | 15ms | 9.86 | 0.0% |
| onnx_dynamic_int8_concurrent | compute_skylake | - | - | - | - |
| onnx_static_int8_agg | compute_skylake | 7ms | 14ms | 9.95 | 0.0% |
| onnx_static_int8_cons | compute_skylake | 7ms | 15ms | 9.81 | 0.0% |
| fastapi_sequential | compute_skylake | 7ms | 14ms | 9.95 | 0.0% |
| fastapi_concurrent | compute_skylake | 7ms | 13ms | 9.99 | 0.0% |
| lightgbm_mlflow_baseline | compute_skylake | 9ms | 19ms | 9.95 | 0.0% |
| lightgbm_mlflow_concurrent | compute_skylake | 9ms | 19ms | 10.00 | 0.0% |
| rayserve_baseline | compute_skylake | 19ms | 42ms | 9.87 | 0.0% |
| rayserve_replica_1 | compute_skylake | 19ms | 41ms | 9.92 | 0.0% |
| rayserve_replica_2 | compute_skylake | 20ms | 43ms | 9.95 | 0.0% |
| rayserve_replica_4 | compute_skylake | 19ms | 47ms | 9.90 | 0.0% |
