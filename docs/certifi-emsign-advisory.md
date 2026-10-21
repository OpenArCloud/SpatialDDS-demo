# certifi, the emSign root, and every path here that runs `pip install`

**GHSA-6q48-6c7w-9gw2, Critical. Handled 2026-10-20.**

## What the advisory actually says

certifi before 2026.10.18 bundles **`emSign ECC Root CA - G3`**. Eight eMudhra
intermediate CAs chaining to that root have compromised private keys, and the
CA has revoked them. Anyone holding one can issue a valid-looking certificate
for any hostname; on-path, that is interception of any TLS traffic verified
against the certifi bundle — which means `requests`, `urllib3`, **and `pip`
itself**.

Two version details matter and both are easy to get wrong:

* **The fix is 2026.10.19, not 2026.10.18.** 2026.10.18 removed *all* emSign
  roots; 2026.10.19 restored the ones that are not affected. The advisory's
  own vulnerable range is `>= 2019.03.09, < 2026.10.19`.
* **So emSign roots in the bundle are not the symptom.** A patched 2026.10.19
  bundle legitimately contains `emSign Root CA - G1`, `emSign Root CA - C1`
  and `emSign ECC Root CA - C3`. The only check that means anything is whether
  **`emSign ECC Root CA - G3`** is present. Grepping for `emSign` reports a
  patched bundle as vulnerable.

## Two trust stores, and only one of them is `pip install -U certifi`

`pip` does not verify TLS against the `certifi` in site-packages. It uses its
own vendored copy at `pip._vendor.certifi`, which moves only when **pip**
moves. So the two halves of `pip install -U pip certifi` fix different things
and neither substitutes for the other:

| upgrading | fixes the trust store used by |
|---|---|
| `certifi` | `requests`, `urllib3`, anything importing certifi |
| `pip` | pip's own package fetches |

Measured here, which is the clearest way to see it: the running bridge
container had `certifi 2026.10.19` in site-packages — clean — while its
`pip 26.2.1` still vendored a bundle containing the affected G3 root. Only
pip 26.2.2 carries the corrected vendored bundle.

## What changed in this repository

Every committed path that runs `pip install` now refreshes pip and certifi
**before its first package install**, because the old trust store is what
fetches its own replacement otherwise.

| path | count | shape |
|---|---|---|
| Dockerfiles | 4 | `RUN python3 -m pip install -U pip certifi` ahead of the first install |
| container bootstrap scripts | 8 | same command, joined with the separator already in use |
| host deploy script | 1 | unconditional, ahead of the CDK dependency install |
| host capture tools | 4 | inside the branch that fetches, so a capture run does not rewrite the host environment every time |
| **total** | **17** | |

`Dockerfile.base`, `robot_tier/Dockerfile`, `deploy/aws/Dockerfile.deploy`,
`bridges/ros2_bridge/Dockerfile.test`; `run_bridge_server_docker.sh`,
`scripts/run_bridge_http_tests_docker.sh`,
`scripts/run_bridge_http_tests_with_logs.sh`, `scripts/run_tests.sh`,
`benchmarks/run_all_benchmarks.sh`, `deploy/aws/deploy.sh`,
`deepsense/`, `nuscenes/` and `multi_operator_fusion/run_docker_demo.sh`;
`tools/capture_{bridge,robot,owm}_session.sh`,
`tools/capture_dispose_divergence.sh`.

`deploy/aws/cdk/cdk.out/` holds CDK's staged copies of `Dockerfile.base`. It
is gitignored and regenerated from the source on the next synth, so it is not
edited here and does not need to be.

## An image already built does not get any of this

Docker layers are immutable. A tag keeps the certifi and pip it was built
with, so **these edits reach a deployment only on rebuild and re-push** — the
advisory says the same thing in one line: *"rebuild container images that
vendor certifi"*. The images this applies to:

| image | rebuilt by |
|---|---|
| `ghcr.io/openarcloud/cyclonedds-python-base:11.0.1-ubuntu22.04` | `Dockerfile.base`, then push to GHCR |
| `spatialdds-robot-tier:latest` | `docker build -t spatialdds-robot-tier:latest -f robot_tier/Dockerfile .` |
| the AWS deploy image | `deploy/aws/Dockerfile.deploy`, via `deploy/aws/deploy.sh` |
| `cyclonedds-python` (local) | `Dockerfile.base` |

A running container is a second step beyond that: it must be **restarted**
onto the rebuilt image. Containers that pip-install at startup (the bridge
stack does) pick the fix up on restart alone, because the prelude now runs
before their first install.

## Measured, 2026-10-20

| environment | pip | certifi | affected G3 root trusted |
|---|---|---|---|
| host (miniforge, python 3.12) — before | 25.0 | 2024.12.14 | yes |
| host — after | 26.2.2 | 2026.10.19 | **no** |
| `dds_bridge` container (running, pre-change) | 26.2.1 | 2026.10.19 | **yes, via pip's vendored bundle** |
| `robot_tier` container (running, pre-change) | 22.0.2 | not installed | no — that pip's vendored bundle predates G3 |

The bridge container is the live example of why the pip half is not optional,
and it is fixed by a restart now that the prelude is committed.
