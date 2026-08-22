# Bridge HTTP tests

End-to-end flow against the DDS-backed bridge: a DDS `CoverageQuery` to discover
the VPS, then HTTP localize and catalog queries through the bridge.

Run it in Docker — the servers expect `/etc/cyclonedds.xml`, which the image
provides and a host normally doesn't:

```bash
# from the repo root
bash run_bridge_http_tests_docker.sh        # plain runner
bash run_bridge_http_tests_with_logs.sh     # pytest variant, captures logs
```

The harness starts `ar_demo/spatialdds_demo_server.py`,
`ar_demo/spatialdds_catalog_server.py` and `bridges/web_bridge/server.py`, and
seeds the catalog from `catalog_seed_austin.json` so results land near the
web UI's default view.

If the plain runner reports `COVERAGE_RESPONSE timeout`, re-run it: its
discovery query is sent once with no retry and can lose the race with DDS
discovery. The pytest variant is steadier.
