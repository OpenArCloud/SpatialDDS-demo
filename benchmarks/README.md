# SpatialDDS Benchmark Suite

This directory contains additive benchmark scripts for evaluating SpatialDDS protocol overhead and scalability.

## Benchmarks

- `bench_latency.py`: Envelope RTT overhead vs raw DDS RTT across payload sizes.
- `bench_discovery.py`: Time from new participant join to first `ANNOUNCE` as service count scales.
- `bench_multioperator.py`: Per-message latency and aggregate throughput as concurrent operator publishers scale.
- `bench_coverage_query.py`: Catalog query RTT as number of registered spatial entries scales.

## Output Files

CSV outputs are written to `benchmarks/results/` by default:

- `latency.csv`: `path,payload_bytes,iteration,latency_ns`
- `discovery.csv`: `num_services,iteration,discovery_time_ns`
- `multioperator.csv`: `num_operators,iteration,msg_latency_ns,total_msgs_per_sec`
- `coverage_query.csv`: `num_entries,iteration,query_time_ns`

Each CSV starts with metadata comments containing machine specs (CPU, RAM, OS, Python, timestamp).

## Quick Run

From repo root:

```bash
cd benchmarks
bash run_all_benchmarks.sh
```

The script:

1. Starts the Docker stack (`docker-compose up -d`), then polls `http://localhost:8088/health`.
2. Runs all benchmark Python inside the `cyclonedds-python` container (not host Python).
3. Generates paper-ready figures in `benchmarks/figures/`.
4. Stops services.

If compose services are not healthy in this repo layout, it falls back to `../run_bridge_server_docker.sh`.

## Individual Runs

```bash
python3 bench_latency.py --iterations 1000 --output results/latency.csv
python3 bench_discovery.py --services 1,5,10,25,50,100 --iterations 50 --output results/discovery.csv
python3 bench_multioperator.py --operators 1,2,5,10,20 --duration 30 --output results/multioperator.csv
python3 bench_coverage_query.py --entries 10,50,100,500,1000 --iterations 100 --output results/coverage_query.csv
```

## Plotting

```bash
python3 plot_results.py --input results/ --output figures/
```

Generated PDFs:

- `figure1_envelope_overhead.pdf`: grouped bars, median latency with p5-p95 error bars.
- `figure2_multioperator_scaling.pdf`: dual-axis latency vs throughput scaling.
- `figure3_coverage_scaling.pdf`: query latency vs catalog size with p5-p95 error bars.

Matplotlib style is `seaborn-v0_8-paper` with single-column ACM-friendly figure width (3.5 in).

## Notes

- All timings use `time.perf_counter_ns()`.
- Warmup runs are executed before recorded iterations.
- Progress and summaries are printed to `stderr` so `stdout` remains pipeline-friendly.
- Full suite execution is container-first: Python benchmark/plot scripts run in Docker.
- Benchmarks are additive and do not modify existing demo code.
