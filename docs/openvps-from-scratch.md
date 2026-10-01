# Rebuilding the OpenVPS deployment from scratch

Two stacks in two repos, and they are not symmetric. The order is forced by one
fact: a new GPU instance has a new private IP and a new security group, and the
demo task's config names both.

```
1  openvps-deploy    CloudFormation   new GPU box, builds on the box   ~35 min
2  restore the map   this repo        from S3, then load + verify      ~5 min
3  SpatialDDS-demo   CDK              Fargate task, pointed at (1)     ~10 min
```

Nothing in `openvps-deploy` needs editing. Its template already pins the ref to
deploy, and the pin and the branch move together in one commit.

## 1 · The GPU stack

```sh
cd ../openvps-deploy
python3 deploy/aws/build.py --check          # template.yaml is generated; fail if stale
aws cloudformation validate-template --template-body file://deploy/aws/template.yaml

aws cloudformation create-stack --stack-name openvps-fountain-2 \
  --template-body file://deploy/aws/template.yaml \
  --capabilities CAPABILITY_IAM --on-failure DO_NOTHING \
  --parameters \
    ParameterKey=Variant,ParameterValue=spatialdds \
    ParameterKey=VpcId,ParameterValue=vpc-0984ff76562a09340 \
    ParameterKey=SubnetId,ParameterValue=subnet-0acafe778bd0eb8ec \
    ParameterKey=SecretsManagerArn,ParameterValue=<the secret> \
    ParameterKey=InstanceType,ParameterValue=g4dn.2xlarge \
    ParameterKey=RootVolumeSize,ParameterValue=200 \
    ParameterKey=MapsVolumeSize,ParameterValue=50 \
    ParameterKey=IdleShutdownMinutes,ParameterValue=30 \
    ParameterKey=HlocRepo,ParameterValue=https://github.com/cvg/Hierarchical-Localization.git \
    ParameterKey=HlocCommit,ParameterValue=c13273b \
    ParameterKey=UpstreamRepo,ParameterValue=https://github.com/OpenArCloud/openvps.git \
    ParameterKey=DefaultLatitude,ParameterValue=30.2849 \
    ParameterKey=DefaultLongitude,ParameterValue=-97.7394 \
    ParameterKey=AttachElasticIp,ParameterValue=false
```

**Leave `UpstreamRef` out.** Empty means "take the variant's pin", which is the
normal case and the whole point: the template decides what gets deployed, so a
from-scratch deploy is a real test of the pinned code rather than of whatever
somebody typed. Setting it overrides that and should be reserved for bisecting.

**Same `SubnetId` as the stack it replaces**, because the subnet fixes the AZ
and the AZ fixes where the maps volume can ever attach.

**Deploy it beside the old one, not over it.** A second stack means the
known-good deployment is still serving while the new one is unproven, and the
rollback is "point the task back". Deleting first turns a bad build into an
outage. `DeletionPolicy: Retain` on the maps volume means the old map survives
either way, but a retained volume is not a running service.

`--on-failure DO_NOTHING` keeps `/var/log/openvps-bootstrap.log` alive if the
bootstrap fails; without it the box is gone before it can be read.

### Watching it, and failing early

The long pole is on the instance: it clones upstream and HLOC, then
`docker compose build` pulls multi-gigabyte CUDA layers. Check the ref first —
it is the cheapest possible confirmation that the right thing is being built,
available minutes in rather than at the end:

```sh
# over SSM
git -C /opt/openvps/openvps log -1 --format='%H %s'
tail -20 /var/log/openvps-bootstrap.log
```

## 2 · The map

```sh
scripts/openvps_restore_map.sh <new-instance-id>
```

Restores the aligned, georeferenced archive from S3, checksums it, unpacks it,
loads it and verifies it. What it is and why those bytes matter:
[`deploy/aws/maps/fountain/README.md`](../deploy/aws/maps/fountain/README.md).

Two traps the script exists to avoid:

**The instance cannot reach S3.** Its role has
`AmazonSSMManagedInstanceCore` and three narrow inline policies — nothing for
S3 at all. `aws s3 cp` on the box fails with no credentials, which presents as a
network problem. The script presigns a URL off the instance and has it run
`curl`.

**`--load` is not implied by the files being there.** Nothing loads a map at
boot, and a localizer holding no map neither localizes nor announces — so
discovery reports no VPS, not a VPS with no map.

## 3 · The Fargate task

A new instance means a new address and a new group, so this is the one place a
file in this repo has to change:

```sh
aws cloudformation describe-stacks --stack-name openvps-fountain-2 \
  --query 'Stacks[0].Outputs[?OutputKey==`PrivateIp`].OutputValue'
aws cloudformation describe-stack-resource --stack-name openvps-fountain-2 \
  --logical-resource-id GpuSecurityGroup --query 'StackResourceDetail.PhysicalResourceId'
```

then in `deploy/aws/config.yaml`:

```yaml
dds_peers: "udp/<new private ip>"
security_group_ids: "<new sg>"
```

and `deploy/aws/deploy.sh`.

`security_group_ids` is what gets RTPS through: the openvps stack's 7400-7500
rule is sourced from its own group, so a task placed inside that group is
covered by it unchanged.

**On a wake, not a rebuild, neither value changes.** A VPC holds an instance's
primary private IPv4 across stop/start, so `config.yaml` is only edited when the
instance is *replaced*. The redeploy is still needed, for an unrelated reason: a
participant does not pick up a peer that was absent when it started, so a task
that outlived the GPU's shutdown never finds the localizer. Use
`--force-new-deployment` and leave the file alone.

## 4 · Proving it

Discovery first, then poses.

```sh
BASE=http://<alb>
curl "$BASE/.well-known/spatialdds/search?geohash=9v6kr"
# -> svc:vps:oarc/<name>;v=b1afa008-6c71-4877-b831-70b113d601dc
```

Then localize the six bundled query frames and compare against
[`baseline-1a608af.md`](../deploy/aws/maps/fountain/baseline-1a608af.md).

**Compare with a tolerance, not for equality.** 1e-7 degrees and 10 mm. RANSAC
inside PnP is not deterministic, so the same frame twice on the *same* build
already differs by ~1e-9 degrees; an equality check fails on a correct
localizer.

What each outcome means:

| Result | Reading |
|---|---|
| Within tolerance on all six | The rebuild changed nothing observable. The deploy routine and the new ref are both good. |
| Shifted consistently, still a coherent track | A real change in the pose pipeline — expected if a camera-model fix now applies to this capture. Re-check against ground truth before accepting it as better. |
| One or two frames move, others identical | A per-camera-model difference. Look at which frames; that is the shape `31b3df8` would produce. |
| `VPS_FAILURE`, or no announce | Map not loaded, or `transform.json` lost its latitude. `--verify` separates those. |

Only after this passes: delete the old stack, and the orphaned volume it leaves
behind.

---

## The current deployment

Recorded because `<instance-id>` in a runbook is the reason this took an
afternoon to reconstruct rather than five minutes.

```
openvps-fountain-2   i-04d28d454b7d35e75   172.31.1.28    sg-0ef313f99840dc12d
                     vol-079ce4dcfe06e1c9b (maps)         us-east-1a
                     UpstreamRefDeployed 31b3df8          deployed 2026-10-01

spatialdds-demo      http://spatia-Servi-aXJc8ECI0qPS-1339332116.us-east-1.elb.amazonaws.com
                     /ar/ is the demo, /static/index.html the fusion dashboard

map                  dataset e6c9dced-7c03-494a-8ac0-581da857c13c
                     map     b1afa008-6c71-4877-b831-70b113d601dc
                     geohash 9v6kr
```

Kept as a rollback until after ISMAR, stopped:

```
openvps-fountain     i-0e6bf67b60e2de5ed   172.31.13.67   sg-022208ef1e0b195e5
                     UpstreamRefDeployed 1a608af
```

To fall back: start that instance, load the map, put `172.31.13.67` and
`sg-022208ef1e0b195e5` back into `deploy/aws/config.yaml`, `deploy.sh`.

**Do not run both at once.** Two instances serving the same map announce the
*same* `service_id` — it carries the map, not the host — so discovery cannot say
which one answered, and a request naming that id is served by whoever replies
first. Fine if they run identical code; silently wrong for any comparison.

## Waking the current deployment

Not a rebuild. Three steps, and `config.yaml` is not one of them:

```bash
aws ec2 start-instances --instance-ids i-04d28d454b7d35e75
aws ec2 wait instance-status-ok --instance-ids i-04d28d454b7d35e75

python3 scripts/openvps_prepare_map.py --instance i-04d28d454b7d35e75 \
    --dataset e6c9dced-7c03-494a-8ac0-581da857c13c \
    --map b1afa008-6c71-4877-b831-70b113d601dc --load --verify

aws ecs update-service --cluster spatialdds-demo-cluster \
    --service spatialdds-demo-service --force-new-deployment
```

Then `BASE=<alb> python3 deploy/aws/smoke_test.py`, which localizes a real frame
against the real VPS and fails if the pose does not come back.

**It will not idle-stop while a map is loaded.** The idle detector counts GPU
processes and a resident localizer holds one, so it logs
`busy (gpu:1proc); resetting idle clock` indefinitely. At $0.752/hr, stop it by
hand:

```bash
aws ec2 stop-instances --instance-ids i-04d28d454b7d35e75
```
