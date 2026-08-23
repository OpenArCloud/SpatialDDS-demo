# SpatialDDS benchmarks

Scripts for measuring SpatialDDS protocol overhead and scalability.

## Three arms

`bench_latency.py` measures three transports so the comparison says something:

| Arm | What it is |
|---|---|
| `spatialdds_typed` | What the demo does: real IDL types on spec-named topics with their §3.3.3 QoS profiles. **These are the first honest SpatialDDS numbers this repo has produced** — every earlier figure measured the envelope, which is not what the spec describes. |
| `spatialdds_envelope` | The old shape. One unkeyed struct on one topic with the payload as a JSON string inside it. Nothing publishes this way any more; it is kept as the baseline. |
| `raw_dds` | A minimal hand-written struct with default QoS — the floor, before any SpatialDDS semantics. |

### Reading the numbers fairly

Both SpatialDDS arms **poll** a reader rather than blocking on one, and each
had its own hardcoded interval — 10 ms in the envelope transport, 20 ms in
the service clients. Left alone, this benchmark compares two poll loops
rather than two transports: the first typed run came out at 22 ms, which was
entirely the client's 20 ms poll. Both are now set to the raw arm's 1 ms, so
what is measured is serialisation and delivery.

Those defaults are a latency floor in the demo itself, and worth knowing: a
reply that arrives in 2 ms is not seen for up to 20.

The measured VPS responder also skips `VPSServiceV15`'s 50–150 ms simulated
localization delay, which is right for a demo and would swamp a benchmark.

### Median round-trip, one machine, 60 iterations

| Payload | envelope | typed | raw DDS |
|---:|---:|---:|---:|
| 1 KiB | 1.97 ms | **1.68 ms** | 2.02 ms |
| 10 KiB | 1.98 ms | **1.68 ms** | 1.96 ms |
| 100 KiB | 3.76 ms | **1.70 ms** | 2.00 ms |
| 500 KiB | 6.84 ms | **1.85 ms** | 1.99 ms |

The shape is the result, not the absolute numbers. The envelope's cost grows
with payload — 1.97 ms to 6.84 ms, ~3.5x — because it JSON-encodes the
payload into a string and copies it. The typed arm is flat (1.68 → 1.85 ms)
because CDR serialises the struct directly, and it tracks raw DDS throughout.

At small payloads the three are indistinguishable; the envelope's overhead is
not a constant tax, it is a tax on data volume, which is exactly the regime
sensor streams live in.

## Benchmarks

- `bench_latency.py`: Round-trip latency across all three arms and payload sizes.
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

## Discovery page size, 1.6 vs 1.7

1.7 changed `disco::CoverageResponse.results` from `sequence<Announce>` to
compact `ServiceSummary` rows. A summary carries identity, coverage and
`manifest_uri`; capabilities, topics and transforms are fetched afterwards from
`manifest_uri` or the service's retained `Announce`. Measured against this
repo's mock VPS, which advertises two coverage elements:

| CoverageResponse row | Bytes | 10 results | 100 results |
|---|---|---|---|
| 1.6 full `Announce` | 1589 | 15.9 KB | 159.2 KB |
| 1.7 `ServiceSummary` | 872 | 8.8 KB | 87.5 KB |

45% smaller at any page size. The saving is the point of the change, not a
regression: a client fetches the full detail only for the service it picks.

```bash
python3 -c "
import json
from spatialdds_test import SpatialDDSLogger, VPSServiceV15
svc = VPSServiceV15(SpatialDDSLogger())
print(len(json.dumps(svc.create_announce()).encode()),
      len(json.dumps(svc.create_service_summary()).encode()))
"
```

## Notes

- Timings use `time.perf_counter_ns()`, after warmup iterations.
- Progress and summaries go to `stderr`, so `stdout` stays pipeline-friendly.
- The benchmark and plot scripts run inside the `cyclonedds-python` container.
