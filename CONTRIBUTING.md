# Contributing to SpatialDDS-demo

We welcome contributions to this project! By contributing, you agree to the terms outlined below.

## License Agreement

By contributing code, documentation, or other materials to this project, you agree that your contributions will be licensed under the same MIT License that covers the project.

## Patent Grant

**Explicit Patent Grant**: By submitting a contribution to this project, you hereby grant to the project maintainers and to all recipients of this software a perpetual, worldwide, non-exclusive, no-charge, royalty-free, irrevocable patent license to make, have made, use, offer to sell, sell, import, and otherwise transfer your contribution, where such license applies only to those patent claims licensable by you that are necessarily infringed by your contribution alone or by combination of your contribution with the work to which such contribution was submitted.

If you institute patent litigation against any entity (including a cross-claim or counterclaim in a lawsuit) alleging that any contribution to this project or the project itself constitutes direct or contributory patent infringement, then any patent licenses granted to you under this agreement for that contribution or project shall terminate as of the date such litigation is filed.

## How to Contribute

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Test your changes (see the test matrix below)
5. Submit a pull request

## Test matrix

Most suites run on the host. The ones needing DDS, ROS 2 or a broker run in
containers. "Test your changes" means the host suites at minimum, plus whichever
container suite covers what you touched.

| Suite | How to run | Needs |
|---|---|---|
| Unit + bridge logic | `python3 -m pytest multi_operator_fusion bridges/ros2_bridge/test_conversions.py bridges/mqtt_bridge bridges/web_bridge/test_router.py bridges/web_bridge/test_client.py bridges/web_bridge/test_dashboard_routes.py bridges/mcap_bridge tests/ nuscenes/test_nuscenes_shapes.py deepsense/test_deepsense_shapes.py` | host — **266 passed, 11 skipped** |
| Interop probe, both directions | `python3 -m unittest tests.test_interop` | Docker (needs `CYCLONEDDS_URI`) |
| Head-of-line isolation | `python3 -m unittest tests.test_head_of_line` | Docker (needs `CYCLONEDDS_URI`) |
| ROS 2 DDS round-trip | `python3 -m unittest bridges.ros2_bridge.test_dds_roundtrip` | Docker |
| MCAP record → replay | `python3 bridges/mcap_bridge/test_live.py` | Docker + `pip install mcap` |
| AR-demo protocol | `cd ar_demo && ./run_all_tests.sh` | host |
| Cesium web UI | `cd web && npm test` | host + Playwright browsers |
| Web bridge HTTP | `bash run_bridge_http_tests_docker.sh` | Docker |
| IDL compile + protocol | `docker run --rm cyclonedds-python` | Docker |
| ROS 2 bridge, all tiers | `bash bridges/ros2_bridge/run_docker_tests.sh` | Docker, emulates amd64 on Apple Silicon |
| MQTT bridge Tier-2 | `cd bridges/mqtt_bridge && docker compose -f docker-compose.test.yaml up --abort-on-container-exit --exit-code-from tests` | Docker + Mosquitto |

On Apple Silicon the published `cyclonedds-python-base` image has no arm64
manifest, so build it locally first:

```bash
docker build -t ghcr.io/openarcloud/cyclonedds-python-base:11.0.1-ubuntu22.04 -f Dockerfile.base .
docker build -t cyclonedds-python .
```

The container suites all take the same shape:

```bash
docker run --rm -v "$PWD:/app" -w /app -e PYTHONPATH=/app \
  -e CYCLONEDDS_URI=file:///etc/cyclonedds.xml cyclonedds-python \
  python3 -m unittest tests.test_interop
```

### Things that look like failures but aren't

- **Don't run `pytest bridges` wholesale.** `test_integration.py` exists under
  both `multi_operator_fusion/` and `bridges/web_bridge/`. Both pass when run
  individually.
- **Duplicate module basenames are a live hazard.** `publisher.py` exists three
  times. `spatialdds_types.py` existed twice until a `sys.path` collision
  silently turned a whole test file into a no-op — it reported "converters
  unavailable" and *skipped*, while having imported the wrong module. The
  nuScenes/DeepSense one is `sensor_types.py` now, and tests that must not be
  shadowed import by file path. A skip that hides a failing test is worse than
  the failure.
- **`ar_demo` scripts are cwd-sensitive.** Run `spatialdds_demo_tests.py` from
  `ar_demo/` (it opens `catalog_seed.json` relatively) and
  `comprehensive_test.py` from the repo root (it compiles `idl/v1.7/*.idl`).
  Wrong directory looks like a failure and isn't.
- **The DDS suites skip loudly without `CYCLONEDDS_URI`.** cyclonedds will
  build a participant on a host with no usable networking config, so the gate
  is the environment variable rather than the import — otherwise they fail
  late and confusingly instead of skipping.
- Three `test_`-prefixed files aren't pytest modules, so "no tests ran" is
  correct: `ros2_bridge/test_mocks.py` is a mock library, and
  `mcap_bridge/test_live.py` and `test_with_deepsense.py` are scripts needing
  live DDS and the DeepSense dataset.
- **Real-time lanes drop samples on purpose.** `DET_RT`, `POSE_RT`,
  `LIDAR_RT`, `IMU_RT` and `RADAR_RT` are BEST_EFFORT per §3.3.3, so a burst
  loses some. A test asserting zero loss on one of those is asserting the
  wrong thing; assert it on a reliable lane.
- The two web specs skip each other by design; see `web/README.md`.
- `run_bridge_http_tests_docker.sh` can report `COVERAGE_RESPONSE timeout`. Its
  discovery query is sent once with no retry. Re-run it, or use the pytest
  variant.
- `idlc -l py` writes nothing for `ar_demo/spatialdds.idl` and ignores `-o`, and
  there's no C++ backend in the image. See `ar_demo/DOCKER_GUIDE.md` for the
  commands that do work.

## Code of Conduct

Please be respectful and professional in all interactions related to this project.

## Questions

If you have questions about contributing, please open an issue or contact the maintainers.