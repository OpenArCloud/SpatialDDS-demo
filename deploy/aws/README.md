# SpatialDDS — AWS Fargate Deployment

One CDK stack, one Fargate task, one ALB. Synthesises a public dashboard
with synthetic multi-operator detections and live fused tracks running
end-to-end on AWS in ~5 minutes.

```
Internet
   │ HTTP (TCP 80)
   ▼
┌───────────────────────────────────────────────┐
│  Application Load Balancer                    │
│  - WebSocket-friendly idle timeout (1h)       │
│  - Health check: GET /api/stats → 200         │
└─────────────────┬─────────────────────────────┘
                  │ → port 8088 on the task
                  ▼
┌───────────────────────────────────────────────┐
│  Fargate task — single task, multi-container  │
│  (all containers share the task's ENI loopback│
│   so CycloneDDS discovery on lo "just works") │
│                                               │
│   web-bridge        :8088  (essential)        │
│   fusion                                      │
│   publisher (synth)                           │
│   recorder           (optional — `recording: true`)│
└───────────────────────────────────────────────┘
                  │
                  ▼ (optional, if recording: true)
                ┌─────┐
                │  S3 │
                └─────┘
```

The Fargate-style topology was validated locally first via
[`deploy/aws/docker-compose.local.yaml`](docker-compose.local.yaml) +
[`smoke_test.py`](smoke_test.py) before any AWS resources are spent.

## Prerequisites

| Tool | Why | Install |
|---|---|---|
| AWS CLI v2 | `aws sts get-caller-identity` for account/region detection | `brew install awscli` |
| AWS CDK v2 | the IaC engine | `npm install -g aws-cdk` |
| Docker | builds the demo image as a CDK asset | https://docker.com |
| Python 3.9+ | runs the CDK app + `deploy.sh` |
| AWS credentials | `aws configure` — needs ECR push, ECS, EC2, IAM, ELB, CloudWatch Logs, optional S3 |

## Quick start

```bash
cd deploy/aws
cp config.yaml.example config.yaml          # edit aws_region / stack_name
./deploy.sh
```

**Pick one demo first.** Both run on one DDS domain, so deploying both means
each one's message window shows the other's traffic — the fusion side
publishes detections continuously. In `config.yaml`:

| | `features:` | Serves |
|---|---|---|
| Multi-operator fusion | `fusion_demo: true`, `ar_demo: false` | canvas dashboard at `/` |
| AR demo | `fusion_demo: false`, `ar_demo: true` | Cesium app at `/ar/` |

The AR demo also wants Cesium Ion credentials in the `ar_demo:` block for
photorealistic tiles; without them it falls back to OpenStreetMap imagery.
They are baked into the bundle at build time and therefore served to every
visitor, so use a token scoped to the deployment's hostname.

Output ends with the dashboard / WebSocket / topics URLs and a one-liner
to point [`smoke_test.py`](smoke_test.py) at the deployed ALB:

```bash
BASE='http://<alb-dns>' python3 deploy/aws/smoke_test.py
```

Tear down when done:

```bash
./destroy.sh           # confirms before destroy
./destroy.sh --yes     # CI-friendly, no prompt
```

## What gets deployed

A single CloudFormation stack named per `config.yaml`'s `stack_name`. The
high-bill resources are:

| Resource | Spec | ~Cost |
|---|---|---|
| Fargate task | 1 vCPU, 2 GB | $0.04 / hour |
| ALB | 1 listener | $0.02 / hour |
| NAT gateway | 1 (private subnets need outbound for `pip install` via image pull) | $0.045 / hour |
| CloudWatch Logs | ~10 MB/day | negligible |

Roughly **$0.10 / hour ≈ $2.40 / day**. A workshop demo run "spin up at
9, tear down at 5" costs under $1.

Everything is in one stack so `destroy.sh` reliably reclaims it. The S3
bucket from `features.recording: true` is `RETAIN`-ed by design — empty
+ delete it from the console if you want it gone.

## Configuration

See [`config.yaml.example`](config.yaml.example) for the full schema. The
defaults work as-is; the most useful knobs:

```yaml
features:
  synthetic_publisher: true
  synthetic_operators: 3                 # how many fake fleet operators
  synthetic_objects_per_operator: 5
  synthetic_rate_hz: 10

  recording: false                       # see "Known limitations" below
```

## Sharing a bus with another deployment

The task's participants find each other inside one network namespace, with
multicast off and no peers — a VPC carries no multicast, so relying on it
gives a deployment that works on a laptop, works on one host, and silently
finds nothing across two.

To span two hosts, set `vpc_id`, `dds_peers` and either
`security_group_ids` or `dds_peer_cidr` in `config.yaml`. Placing this task in the peer's VPC avoids peering entirely and
drops the second NAT gateway; the peer list is rendered into the Cyclone
config at container start, because it names addresses that change when a peer
instance is replaced.

**Only this side needs a peer list.** Measured in a two-host container test,
with `<Peers>` left empty on the other end and this side naming it as a bare
`udp/HOST` with no port: every participant on that host was discovered, a
150 KB image made a localize round trip with its SHA-256 intact, and a
departure propagated (the service left `search` within seconds of the peer
stopping). Recovery is automatic in both directions that matter for a GPU
instance that scales to zero — the peer restarting on the same address was
rediscovered in ~16s, and a participant that joined the peer *after* this task
was already running showed up in ~18s.

Naming this task in the other end's peer list too is harmless and makes
discovery converge sooner, but it is not required — which is the point, since
that side is not ours to reconfigure.

The peer is the `spatialdds` variant of
[openvps-deploy](https://github.com/OpenArCloud/openvps-deploy), whose GPU
instance runs OpenVPS's map localizer as a DDS participant alongside a copy of
this bridge. This has been run end to end — see *Running against a real
OpenVPS* below for the recipe and what it measured.

**That side needs no code change.** `DdsPeers` and `AttachElasticIp` are stack
parameters there, so telling it about this task is deploy-time configuration.
Its RTPS ingress rules exist too, but are sourced from its *own* security
group — written for two GPU hosts discovering each other, not a peer outside
the group. Setting `security_group_ids` here puts this task inside that group,
where those rules cover it as written. An Elastic IP is not needed either: its
own description notes the private IP already survives stop/start, and a task
in the same VPC reaches it that way.

## Running against a real OpenVPS

Done once, on `us-east-1`. The AR demo's **Localize with Image** button sends a
real photograph from the scan a map was built from; with a real localizer on
the bus the pose comes back from the pixels rather than from the stand-in.

**1. Deploy the localizer.** Into *this task's VPC*, so no VPC peering is
needed, and a public subnet, since that stack has no NAT gateway:

```sh
cd ../openvps-deploy/deploy/aws
aws cloudformation create-stack --stack-name openvps-spatialdds \
  --template-body file://template.yaml --capabilities CAPABILITY_IAM \
  --on-failure DO_NOTHING \
  --parameters ParameterKey=Variant,ParameterValue=spatialdds \
               ParameterKey=VpcId,ParameterValue=<this task's VPC> \
               ParameterKey=SubnetId,ParameterValue=<a public subnet in it> \
               ParameterKey=SecretsManagerArn,ParameterValue=<the secret>
```

About 35 minutes: most of it builds the localizer image. `--on-failure
DO_NOTHING` keeps `/var/log/openvps-bootstrap.log` alive if it fails.

**2. Give it a map.** Either build one through MapBuilder, or drop a map you
already have onto the maps volume, which skips MapBuilder and its auth
entirely. The localizer scans `/home/ubuntu/data/maps` for dataset directories
containing `status.json` and `hlocMaps/<map-id>/`, each with `config.yaml` and
`transform.json` — the layout MapBuilder already produces, so a downloaded map
can be copied across as-is (a presigned S3 URL and `curl` is enough). Then:

```sh
python3 ../../scripts/openvps_prepare_map.py --instance <id> \
  --dataset <dataset-id> --map <map-id> --load
```

A restart needs this too. Nothing loads a map on boot, and a localizer holding
no map neither localizes nor announces, so discovery reports no VPS at all. The
script's `--align`, `--transform` and `--verify` cover the rest of the map-side
preparation.

**`transform.json` is not optional.** Coverage in 1.7 is entirely geographic,
so a map with no geodetic anchor has nothing truthful to advertise and that
binding declines to announce it. It stays localizable over HTTP and invisible
to discovery — correct, and confusing if unexpected.

**3. Point this task at it.** In `config.yaml`, with the instance's *private*
IP, then `./deploy.sh`:

```yaml
dds_peers: "udp/10.0.62.182"
security_group_ids: "sg-0096e778676953888"   # the openvps stack's own group
```

`security_group_ids` is what gets RTPS through: that stack's 7400-7500 rule is
sourced from its own security group, written for two GPU hosts finding each
other, so a task placed inside the group is covered by it unchanged. Nothing in
`openvps-deploy` needs editing.

**4. Check it.** Discovery first, from the geohash cell containing the map's
anchor, then a localize naming what discovery returned:

```sh
curl "$BASE/.well-known/spatialdds/search?geohash=<cell>"
# -> svc:vps:oarc/<name>;v=<map-id>
```

Measured: discovery inside ~20 s of the announce, and 3.6-5.7 s per localize
for a 180-270 KB JPEG — decode, NetVLAD retrieval, SuperGlue matching and PnP
on a T4. Distinct frames give distinct poses and a repeated frame reproduces
its pose exactly, which is the check that the pixels are being used at all: a
stand-in returning the prior plus jitter passes every other test.

### Three things that will bite

* **The private IP changes on stop/start.** `dds_peers` names an address, so
  restarting the instance means editing `config.yaml` and redeploying this
  task. An Elastic IP would remove the step; the stack has `AttachElasticIp`
  for it.
* **The instance does not idle-stop with a map loaded.** Its idle detector
  counts GPU processes and a resident localizer holds one, so it logs
  `busy (gpu:1proc); resetting idle clock` every five minutes indefinitely.
  At $0.752/hr, stop it by hand when finished.
* **Two VPS services on one bus is the normal case here**, since this task
  runs its own stand-in. A request names the service it wants and both ends
  honour that name, so the stand-in leaves OpenVPS's requests alone — but a
  request naming nobody is answered by whoever replies first, and co-located
  beats remote every time.

## Topology — why a single task

Fargate awsvpc networking gives each task its own ENI but **every
container in a task shares the loopback**. CycloneDDS multicast on `lo`
discovers all peers immediately — no service mesh, no manual peer
configuration, no DDS routing service. The Dockerfile bakes a
loopback-only [`cyclonedds.xml`](cyclonedds.xml) so DDS chatter never
escapes the task. The whole topology was proven first by the docker-
compose stack, where `network_mode: service:web-bridge` reproduces the
same "containers share lo" model on a laptop.

If you split this into multiple Fargate services, you'd need explicit
DDS peer configuration (or a DDS routing service); both are major
escalations from "just works" — keep it as one task.

## Known limitations

- **No HTTPS by default.** The ALB listens on port 80. Adding HTTPS
  needs an ACM certificate + a custom domain (Route53 hosted zone) or
  a self-signed cert. The `features.custom_domain` / `hosted_zone_id`
  config keys are reserved for the production extension; the v0
  stack ignores them. Wire them up in `spatialdds_stack.py` when
  ready (`elbv2.ApplicationListener.add_certificates(...)`).
- **No auto-scaling.** `desired_count = 1`. For a demo, that's the
  right answer — DDS multicast on a Fargate task means you can't
  trivially add a second task and expect cross-task discovery to
  work. Production multi-instance would route through MQTT (see the
  MQTT bridge) or a DDS gateway.
- **MCAP recording is local-only in v0.** The `recorder` container,
  when enabled, writes to `/tmp/recording.mcap` inside the task. The
  CDK stack provisions an S3 bucket and grants the task write access,
  and exposes the bucket name via `SPATIALDDS_RECORDING_BUCKET`, but
  `recorder.py` doesn't yet implement S3 rotation/upload. Tracked as
  a follow-on. To pull the file off the task while it's running, use
  `aws ecs execute-command`.
- **No WAF / VPC endpoints / security hardening.** This is a demo
  deployment. Production would add WAF rules, restrict the security
  group, and enable VPC endpoints to keep ECR / Logs traffic off the
  public internet.

## Files in this directory

```
deploy/aws/
├── README.md                    # this file
├── Dockerfile.deploy            # single image used by all containers in the task
├── cyclonedds.xml               # loopback-only DDS config (baked into the image)
├── docker-compose.local.yaml    # 4-container local equivalent (validate before deploying)
├── smoke_test.py                # asserts /health + ≥2 operators + fused tracks
├── config.yaml.example          # template config (copy → config.yaml + edit)
├── deploy.sh                    # one-button deploy
├── destroy.sh                   # one-button teardown
└── cdk/
    ├── app.py                   # CDK app entry point
    ├── spatialdds_stack.py      # the actual stack
    ├── cdk.json                 # CDK config
    └── requirements.txt         # aws-cdk-lib, constructs, PyYAML
```

## Validation order, in case something breaks

1. **Local docker-compose first.** If `docker compose up` on
   `docker-compose.local.yaml` doesn't pass `smoke_test.py`, AWS won't
   either. Most demo issues live here, not in the CDK stack.
2. **`cdk synth`** before deploying. Runs the stack purely in-process
   and emits CloudFormation YAML to `cdk.out/` — catches Python errors
   and CDK-API misuse without touching AWS.
3. **`cdk deploy`** for real. ~5–10 minutes the first time (mostly the
   ECR image push); subsequent deploys with the same image hash are
   1–2 minutes.
4. **Hit the deployed ALB with `smoke_test.py BASE=...`** — same
   assertions you ran locally.

## The Cesium bundle

Built in the image (`Dockerfile.deploy` stage 1) and served by the bridge at
`/ar`. Two consequences worth knowing:

* **Vite bakes `VITE_*` env into the bundle at build time**, so the Ion token
  is a build arg. Without it the app still runs — plain globe instead of
  photorealistic tiles, which the UI already has a toggle for. Scope the token
  to this deployment's domains in the Ion console; it ships in the bundle
  either way, as every client-side map credential does.
* **The bridge URL is deliberately not baked in.** The app falls back to its
  own origin, which is right for a deployment where one process serves the
  bundle and the API behind one load balancer.

## Still HTTP

The ALB listens on port 80. That is fine for a demo you open directly and
wrong for anything embedded or shared, and Ion is happier over HTTPS. Adding
it needs a certificate and a domain, which are deployment decisions rather
than repo ones — `features.custom_domain` / `hosted_zone_id` are the
placeholders for it.
