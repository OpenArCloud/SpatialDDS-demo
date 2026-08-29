"""SpatialDDS demo — single Fargate task with multiple containers.

Topology mirrors ``deploy/aws/docker-compose.local.yaml`` exactly: every
container in the task shares the task's ENI loopback, so CycloneDDS
discovery on lo works out-of-the-box. The local docker-compose stack is
the smoke-test rig that validated this topology before this stack
deploys it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from aws_cdk import (
    CfnOutput,
    Duration,
    RemovalPolicy,
    Stack,
)
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_ecr_assets as ecr_assets
from aws_cdk import aws_ecs as ecs
from aws_cdk import aws_ecs_patterns as ecs_patterns
from aws_cdk import aws_elasticloadbalancingv2 as elbv2
from aws_cdk import aws_logs as logs
from aws_cdk import aws_s3 as s3
from constructs import Construct


_REPO_ROOT = Path(__file__).resolve().parents[3]
_DOCKERFILE_REL = "deploy/aws/Dockerfile.deploy"


class SpatialDDSStack(Stack):
    """One ECS Fargate task running the demo end to end.

    Containers (selected by command from one shared image):

      * ``web-bridge``   — primary, exposes port 8088 via the ALB.
      * ``fusion``       — multi-operator fusion service.
      * ``publisher``    — synthetic Detection3D generator (3 operators).
      * ``recorder``     — optional, gated by ``features.recording``.
                            v0 writes locally only; S3 upload is a TODO.
    """

    def __init__(self, scope: Construct, construct_id: str, *,
                 config: dict, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        features: dict = dict(config.get("features") or {})
        cpu = int(config.get("cpu", 1024))
        memory_mib = int(config.get("memory", 2048))

        # ── VPC ───────────────────────────────────────────────────────────
        # Two AZs (ALB minimum); one NAT to keep the bill modest.
        # Own VPC by default. Name an existing one to put this task beside
        # something already deployed — an OpenVPS instance, say — so the two
        # can share a DDS bus without VPC peering, and so this stack stops
        # paying for a second NAT gateway.
        #
        # `from_lookup` needs a concrete account and region, which app.py
        # supplies from the environment; a stack synthesised without them
        # cannot resolve an existing VPC and says so rather than guessing.
        existing_vpc_id = str(config.get("vpc_id") or "").strip()
        if existing_vpc_id:
            vpc = ec2.Vpc.from_lookup(self, "Vpc", vpc_id=existing_vpc_id)
        else:
            vpc = ec2.Vpc(
                self, "Vpc",
                max_azs=2,
                nat_gateways=1,
            )

        # ── ECS cluster + log group ──────────────────────────────────────
        cluster = ecs.Cluster(
            self, "Cluster",
            vpc=vpc,
            cluster_name=f"{construct_id}-cluster",
        )

        log_group = logs.LogGroup(
            self, "Logs",
            log_group_name=f"/ecs/{construct_id}",
            removal_policy=RemovalPolicy.DESTROY,
            retention=logs.RetentionDays.ONE_WEEK,
        )

        # ── Demo image (built locally from Dockerfile.deploy) ────────────
        # Note: the Dockerfile's FROM is
        # ``ghcr.io/openarcloud/cyclonedds-python-base:11.0.1-ubuntu22.04``.
        # ``deploy.sh`` builds that tag locally for linux/amd64 before
        # invoking CDK. Running ``cdk deploy`` by hand needs the same step
        # first — ``docker build --platform=linux/amd64 -f Dockerfile.base
        # -t <same-tag> .`` from the repo root — because the registry has
        # only the superseded 0.10.5 tag.
        # AR-demo settings: which place the mock VPS claims to cover, which
        # catalogue it serves, and its Cesium credentials. Read here rather
        # than beside the other container config because the image asset
        # below needs it — Vite bakes the Ion values in at build time.
        ar: Dict[str, Any] = dict(config.get("ar_demo") or {})

        # Cesium Ion credentials for the AR bundle, if configured. Vite bakes
        # `VITE_*` into the bundle at build time, so these have to be build
        # args — there is no run-time way to supply them.
        #
        # They end up in a publicly served bundle, as every client-side map
        # credential does. Scope the token to this deployment's hostname in
        # the Ion console; do not reuse a developer's personal token. Without
        # them the app falls back to OpenStreetMap imagery and hides the
        # photorealistic-tiles toggle, which is a working demo, just a
        # plainer one.
        build_args = {}
        ion_token = str(ar.get("cesium_ion_token") or "").strip()
        ion_asset = str(ar.get("cesium_ion_asset_id") or "").strip()
        if ion_token:
            build_args["VITE_CESIUM_ION_TOKEN"] = ion_token
        if ion_asset:
            build_args["VITE_CESIUM_ION_ASSET_ID"] = ion_asset

        image_asset = ecr_assets.DockerImageAsset(
            self, "Image",
            directory=str(_REPO_ROOT),
            file=_DOCKERFILE_REL,
            platform=ecr_assets.Platform.LINUX_AMD64,
            build_args=build_args or None,
        )
        image = ecs.ContainerImage.from_docker_image_asset(image_asset)

        # ── Task definition ──────────────────────────────────────────────
        task_def = ecs.FargateTaskDefinition(
            self, "TaskDef",
            cpu=cpu,
            memory_limit_mib=memory_mib,
        )

        common_env: Dict[str, str] = {
            "SPATIALDDS_DDS_DOMAIN": "0",
            # The bootstrap manifest the bridge serves at
            # /.well-known/spatialdds/bootstrap is driven by its own
            # SPATIALDDS_BOOTSTRAP_* variables, which default to domain 1.
            # Unset, a Layer-1 client that followed this deployment's own
            # bootstrap would join domain 1 and find nothing, because the
            # task publishes on domain 0. Keep the two in step.
            "SPATIALDDS_BOOTSTRAP_DOMAIN": "0",
            "SPATIALDDS_BOOTSTRAP_SITE": construct_id,
            "SPATIALDDS_TRANSPORT": "dds",
            "CYCLONEDDS_URI": "file:///etc/cyclonedds.xml",
            # Unicast RTPS peers, for putting this task on a bus that spans
            # more than itself — an OpenVPS instance running its own
            # participants, say. Empty is the single-task case and is what
            # every container here has used until now.
            "SPATIALDDS_DDS_PEERS": str(config.get("dds_peers") or ""),
            "PYTHONUNBUFFERED": "1",
        }

        # Container 1: web bridge (the externally-reachable one).
        web_container = task_def.add_container(
            "web-bridge",
            image=image,
            command=[
                "python3", "-m", "bridges.web_bridge",
                "--port", "8088", "--domain", "0",
            ],
            essential=True,
            environment={
                **common_env,
                "SPATIALDDS_BRIDGE_ALLOW_PUBLISH": "1",
                # Hardcoded synthetic VPS announce so /health returns
                # something the Cesium UI can render even before the
                # publisher reaches steady state.
                "SPATIALDDS_VPS_SERVICE_ID": "svc:vps:demo/synthetic",
                "SPATIALDDS_VPS_SERVICE_NAME": "SyntheticVPS",
                "SPATIALDDS_VPS_COVERAGE_BBOX": "-12,-12,12,12",
                "SPATIALDDS_VPS_MAP_FQN": "map/synthetic",
                "SPATIALDDS_VPS_MAP_ID": "synthetic-map",
                "SPATIALDDS_DEMO_MANIFEST_URI":
                    "spatialdds://vps.example.com/zone:sf-downtown/manifest:vps",
            },
            logging=ecs.LogDriver.aws_logs(
                stream_prefix="web-bridge",
                log_group=log_group,
            ),
            health_check=ecs.HealthCheck(
                command=["CMD-SHELL",
                          "curl -fsS http://localhost:8088/api/stats >/dev/null || exit 1"],
                interval=Duration.seconds(30),
                timeout=Duration.seconds(5),
                retries=3,
                start_period=Duration.seconds(20),
            ),
        )
        web_container.add_port_mappings(
            ecs.PortMapping(container_port=8088, protocol=ecs.Protocol.TCP),
        )

        # Container 2: fusion service. Non-essential — if it crashes the
        # web bridge keeps serving (the dashboard just won't show fused
        # tracks until it restarts).
        #
        # Gated, because the two demos share one DDS domain when both run and
        # the fusion side publishes detections and entity bindings
        # continuously. A viewer of the AR demo then watches a message window
        # full of another demo's traffic. They are separate demos; run one.
        if features.get("fusion_demo", True):
            task_def.add_container(
                "fusion",
                image=image,
                command=[
                    "python3", "-m", "multi_operator_fusion.fusion_service",
                    "--domain", "0", "--quiet",
                ],
                essential=False,
                environment=common_env,
                logging=ecs.LogDriver.aws_logs(
                    stream_prefix="fusion",
                    log_group=log_group,
                ),
            )

        # Containers for the AR demo: a VPS and a content catalogue, the two
        # services the Cesium app discovers and calls. Both announce
        # themselves, so the browser finds them the way any client would —
        # `/.well-known/spatialdds/search` issues a CoverageQuery and these
        # answer it — rather than the bridge naming a service it was
        # configured with.
        #
        # Both are non-essential: if either stops, the web bridge and the
        # fusion demo carry on, and discovery correctly reports that nothing
        # covers the area rather than pretending otherwise.
        if features.get("ar_demo", False):
            ar_env = {
                **common_env,
                "SPATIALDDS_VPS_SERVICE_ID": ar.get("vps_service_id", "svc:vps:demo/austin-downtown"),
                "SPATIALDDS_VPS_SERVICE_NAME": ar.get("vps_service_name", "MockVPS-Austin"),
                "SPATIALDDS_VPS_COVERAGE_BBOX": ar.get("coverage_bbox", "-97.75,30.27,-97.72,30.29"),
                "SPATIALDDS_VPS_MAP_FQN": ar.get("map_fqn", "map/austin"),
                "SPATIALDDS_VPS_MAP_ID": ar.get("map_id", "austin-map"),
                "SPATIALDDS_DEMO_MANIFEST_URI": ar.get(
                    "manifest_uri",
                    "spatialdds://vps.example.com/zone:austin-downtown/manifest:vps"),
            }
            task_def.add_container(
                "vps",
                image=image,
                command=["python3", "ar_demo/spatialdds_demo_server.py", "--summary-only"],
                essential=False,
                environment=ar_env,
                logging=ecs.LogDriver.aws_logs(
                    stream_prefix="vps", log_group=log_group),
            )
            task_def.add_container(
                "catalog",
                image=image,
                command=["python3", "ar_demo/spatialdds_catalog_server.py",
                         "--summary-only",
                         "--seed", ar.get("catalog_seed",
                                          "bridges/web_bridge/tests/catalog_seed_austin.json")],
                essential=False,
                environment=ar_env,
                logging=ecs.LogDriver.aws_logs(
                    stream_prefix="catalog", log_group=log_group),
            )

        # Container 3: synthetic publisher (toggle via features.synthetic_publisher).
        if features.get("fusion_demo", True) and features.get("synthetic_publisher", True):
            task_def.add_container(
                "publisher",
                image=image,
                command=[
                    "python3", "-m", "multi_operator_fusion.synthetic_publisher",
                    "--domain", "0",
                    "--operators", str(features.get("synthetic_operators", 3)),
                    "--objects-per-operator",
                    str(features.get("synthetic_objects_per_operator", 5)),
                    "--rate", str(features.get("synthetic_rate_hz", 10)),
                ],
                essential=False,
                environment=common_env,
                logging=ecs.LogDriver.aws_logs(
                    stream_prefix="publisher",
                    log_group=log_group,
                ),
            )

        # Container 4 (optional): MCAP recorder. v0 writes inside the task
        # only — S3 rotation/upload isn't wired into recorder.py yet
        # (tracked TODO). Enable just to verify the recorder runs in the
        # task topology; download via ``ecs execute-command`` if needed.
        recording_bucket = None
        if features.get("fusion_demo", True) and features.get("recording", False):
            bucket_name = features.get("s3_bucket_name") or None
            recording_bucket = s3.Bucket(
                self, "RecordingBucket",
                bucket_name=bucket_name,
                versioned=False,
                block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
                encryption=s3.BucketEncryption.S3_MANAGED,
                removal_policy=RemovalPolicy.RETAIN,
            )
            recording_bucket.grant_read_write(task_def.task_role)

            task_def.add_container(
                "recorder",
                image=image,
                command=[
                    "python3", "-m", "bridges.mcap_bridge.recorder",
                    "/tmp/recording.mcap",
                    "--domain", "0",
                    "--duration",
                    str(int(features.get("recording_duration_s", 600))),
                ],
                essential=False,
                environment={
                    **common_env,
                    "SPATIALDDS_RECORDING_BUCKET": recording_bucket.bucket_name,
                },
                logging=ecs.LogDriver.aws_logs(
                    stream_prefix="recorder",
                    log_group=log_group,
                ),
            )

        # ── ALB-fronted Fargate service ──────────────────────────────────
        # ``ApplicationLoadBalancedFargateService`` wires up the ALB,
        # target group, and Fargate service in one go; we point at the
        # web bridge's port mapping.
        service = ecs_patterns.ApplicationLoadBalancedFargateService(
            self, "Service",
            cluster=cluster,
            task_definition=task_def,
            desired_count=1,
            public_load_balancer=True,
            assign_public_ip=False,            # task lives in private subnet
            service_name=f"{construct_id}-service",
            listener_port=80,
            # Send ALB → web bridge container, port 8088.
            target_protocol=elbv2.ApplicationProtocol.HTTP,
        )

        # ── Cross-host DDS, when peers are configured ────────────────────
        # RTPS is UDP. Cyclone's default port range for domain 0 starts at
        # 7400; discovery and user traffic both live in it. Opened only when
        # `dds_peers` is set, so a single-task deployment keeps a security
        # group that allows nothing inbound but the ALB.
        #
        # The source is a CIDR rather than a peer security group because the
        # other end is a different stack — possibly a different VPC — and
        # naming its SG here would couple the two deployments' templates.
        dds_peers = str(config.get("dds_peers") or "").strip()
        dds_peer_cidr = str(config.get("dds_peer_cidr") or "").strip()
        if dds_peers:
            if not dds_peer_cidr:
                raise ValueError(
                    "dds_peers is set but dds_peer_cidr is not: RTPS is UDP and "
                    "the task's security group has to allow the peer in, or "
                    "discovery completes one way and no sample is ever exchanged"
                )
            service.service.connections.allow_from(
                ec2.Peer.ipv4(dds_peer_cidr),
                ec2.Port.udp_range(7400, 7500),
                "RTPS discovery and user traffic from the DDS peer",
            )

        # WebSocket + long-lived stream connections need a longer ALB idle
        # timeout than the 60s default. /v1/stream and /ws stay open
        # indefinitely; 1h covers a workshop demo without churn.
        service.load_balancer.set_attribute(
            "idle_timeout.timeout_seconds", "3600",
        )

        # Health check targets the bridge's stats endpoint (cheap, no DDS
        # lookup required, returns immediately).
        service.target_group.configure_health_check(
            path="/api/stats",
            healthy_http_codes="200",
            interval=Duration.seconds(30),
            timeout=Duration.seconds(5),
            healthy_threshold_count=2,
            unhealthy_threshold_count=3,
        )

        # ── Outputs ──────────────────────────────────────────────────────
        alb_dns = service.load_balancer.load_balancer_dns_name
        CfnOutput(
            self, "DashboardURL",
            value=f"http://{alb_dns}/static/index.html",
            description="SpatialDDS web dashboard (debug UI for the /ws protocol)",
        )
        if features.get("ar_demo", False):
            CfnOutput(
                self, "ARDemoURL",
                value=f"http://{alb_dns}/ar/",
                description="Cesium AR demo — discovery, localize and catalogue over REST",
            )
        CfnOutput(
            self, "HealthURL",
            value=f"http://{alb_dns}/health",
            description="Web bridge /health endpoint",
        )
        CfnOutput(
            self, "WebSocketURL",
            value=f"ws://{alb_dns}/ws",
            description="Generic SpatialDDS WebSocket (subscribe / publish / list_topics)",
        )
        CfnOutput(
            self, "TopicsAPI",
            value=f"http://{alb_dns}/api/topics",
            description="REST topic discovery",
        )
        CfnOutput(
            self, "BaseURL",
            value=f"http://{alb_dns}",
            description="Pass to deploy/aws/smoke_test.py via BASE=...",
        )
        if recording_bucket is not None:
            CfnOutput(
                self, "RecordingBucketName",
                value=recording_bucket.bucket_name,
                description="S3 bucket for MCAP recordings (v0: not yet auto-uploaded)",
            )
