#!/usr/bin/env python3
"""CDK app entry point for the SpatialDDS Fargate deployment.

Reads ``deploy/aws/config.yaml`` (override path with the
``-c config_file=...`` context flag) and synthesises a single
``SpatialDDSStack`` into the configured region/account.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import aws_cdk as cdk
import yaml

# Make sibling modules importable when ``cdk`` invokes us from /this/ dir.
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from spatialdds_stack import SpatialDDSStack  # noqa: E402


def _resolve_config_path(app: cdk.App) -> Path:
    """Pick the config file: CLI context wins, else ../config.yaml."""
    ctx = app.node.try_get_context("config_file")
    if ctx:
        return Path(ctx).expanduser().resolve()
    return _HERE.parent / "config.yaml"


def main() -> None:
    app = cdk.App()

    config_path = _resolve_config_path(app)
    if not config_path.is_file():
        print(f"[cdk] config file not found: {config_path}", file=sys.stderr)
        print("      copy deploy/aws/config.yaml.example to config.yaml and edit",
              file=sys.stderr)
        sys.exit(1)

    with open(config_path) as f:
        config = yaml.safe_load(f) or {}

    stack_name = str(config.get("stack_name") or "spatialdds-demo")
    region = str(config.get("aws_region") or os.getenv("CDK_DEFAULT_REGION") or "us-east-1")
    account = os.getenv("CDK_DEFAULT_ACCOUNT")  # filled by ``cdk deploy``

    SpatialDDSStack(
        app, stack_name,
        config=config,
        env=cdk.Environment(account=account, region=region),
        description="SpatialDDS demo — single Fargate task with web bridge, fusion, "
                     "synthetic publisher, optional MCAP recorder",
    )

    app.synth()


if __name__ == "__main__":
    main()
