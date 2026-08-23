# SpatialDDS ↔ ROS 2 Bridge (v0)

A bidirectional bridge between SpatialDDS topics and ROS 2 topics. A robot
publishing standard ROS 2 messages gets fed onto the SpatialDDS bus; data
flowing on the SpatialDDS bus (from any demo, or from the multi-operator
fusion service) gets republished as ROS 2 messages.

## What v0 covers

Five message types end-to-end (encoder + decoder + tests), plus the
multi-operator fusion service's reverse-direction case:

| Direction | ROS 2 type | SpatialDDS type | QoS | SpatialDDS topic |
|---|---|---|---|---|
| ROS 2 → SpatialDDS | `geometry_msgs/PoseStamped` | `framed_pose` | `POSE_RT` | `spatialdds/{op}/ego/pose/v1` |
| ROS 2 → SpatialDDS | `sensor_msgs/NavSatFix` | `geopose` + `navsat_status` | `POSE_RT` | `spatialdds/{op}/geo/{sensor}/pose/v1` |
| ROS 2 → SpatialDDS | `sensor_msgs/Imu` | `imu_sample` | `IMU_RT` | `spatialdds/{op}/imu/{sensor}/sample/v1` |
| ROS 2 → SpatialDDS | `sensor_msgs/CompressedImage` | `video_frame` + `blob_chunk` | `VIDEO_LIVE` | `spatialdds/{op}/vision/{sensor}/frame/v1` |
| ROS 2 → SpatialDDS | `vision_msgs/Detection3DArray` | `detection3d` | `DET_RT` | `spatialdds/{op}/sensing/detection3d/v1` |
| SpatialDDS → ROS 2 | `detection3d` / `fused_track` | (decoded) | | → `vision_msgs/Detection3DArray` |
| SpatialDDS → ROS 2 | `framed_pose` | (decoded) | | → `geometry_msgs/PoseStamped` |
| SpatialDDS → ROS 2 | `geopose` | (decoded) | | → `sensor_msgs/NavSatFix` |
| SpatialDDS → ROS 2 | `imu_sample` | (decoded) | | → `sensor_msgs/Imu` |
| SpatialDDS → ROS 2 | `video_frame` + its blob | (decoded) | | → `sensor_msgs/CompressedImage` |

Two rows are worth reading twice. **NavSatFix becomes two samples**: `GeoPose`
has no fix-status field, and §3.3.2 registers `navsat_status` as its
"companion to GeoPose", so the bridge publishes both rather than inventing a
field. **CompressedImage becomes a frame plus blob chunks**: a `VisionFrame`
is metadata and a `BlobRef`, and the bytes travel on
`spatialdds/blob/chunk/v1` — the spec is explicit that heavy content is never
inlined.

Deferred to follow-up PRs (each is its own story): `PointCloud2`/`LidarFrame`
with binary blob handling, `CameraInfo` with latched-meta semantics,
`LaserScan`, `tf2`/`PlannedTrajectory`, full Imu and CompressedImage in the
reverse direction.

## Why it's small

The bridge is a **typed adapter**: it resolves each ROS 2 message onto a
§3.3.2 type, builds the payload into that type, and writes it on the topic's
§3.3.3 profile. A payload that is not a well-formed sample fails at the
bridge, attributed to the topic it arrived on.

The conversion layer itself produces and consumes plain dicts — no DDS
topic creation, no IDL generation, no new dataclasses in the shared
`nuscenes/spatialdds_types.py`. When a real consumer needs typed access to
a new SpatialDDS type (`FramedPose`, `ImuSample`), promoting the dict to
a dataclass at that point is straightforward.

The conversion layer is **duck-typed** — `ros2_to_spatialdds.py`,
`spatialdds_to_ros2.py`, and `frame_mapping.py` import zero ROS 2 packages.
They operate on objects with the right field names, which is what both
real `rclpy` messages and the mocks in `test_mocks.py` provide. Tier-1
tests run anywhere Python + `pytest` are available.

## Layout

```
bridges/ros2_bridge/
├── __init__.py
├── frame_mapping.py            # tf2 frame_id ↔ FrameRef (deterministic UUIDv5)
├── ros2_to_spatialdds.py       # encoders: 5 ROS 2 types → payload dicts (NO ros2 imports)
├── spatialdds_to_ros2.py       # decoders: payload dicts → mock ROS 2 dataclasses (NO ros2 imports)
├── ../../spatialdds_demo/       # typed_transport, payloads, blob, qos_profiles
│                               # (RELIABLE+KEEP_ALL, reuses MCAP bridge factories)
├── bridge_node.py              # rclpy node — only file with ROS 2 imports
├── test_mocks.py               # Mock ROS 2 message classes (no ros2 imports)
├── test_conversions.py         # Tier-1 pytest, no ROS 2, no DDS  →  31 tests
├── test_dds_roundtrip.py       # Tier-2 pytest + cyclonedds, no ROS 2  →  7 tests
├── verify_mocks.py             # One-time mock-vs-real-ROS-2 fidelity check
└── README.md                   # this file
```

## Testing tiers

| Tier | Dependencies | What it tests | Status |
|------|-------------|--------------|--------|
| **1** | `pytest` only | Every encoder + decoder + frame mapping. JSON-schema shape, quaternion convention, field extraction, top-hypothesis selection, REP-145 sentinels. | ✅ 31/31 passing |
| **2** | `pytest` + `cyclonedds` | Real DDS round-trip across five typed topics: encode → publish → DDS → subscribe → decode, including image bytes reassembled from blob chunks. Burst tests assert the contract both ways — a reliable lane loses nothing, a real-time lane may drop but never corrupts. | ✅ 7/7 passing |
| **3** | full ROS 2 workspace (`rclpy`, `sensor_msgs`, `geometry_msgs`, `vision_msgs`) | Bridge node wiring, real `ros2 topic pub` / `ros2 topic echo` end-to-end. | 🟡 stub provided — run instructions below |
| **3b** | one-time, ROS 2 workspace | `verify_mocks.py` — confirms the mocks in `test_mocks.py` match real ROS 2 IDL field names. | 🟡 run when bridge first deploys |

### Run Tier 1 (host)

```bash
python3 -m pytest -q bridges/ros2_bridge/test_conversions.py
```

### Run Tier 2 (Docker — needs CycloneDDS)

```bash
docker run --rm --network host \
  -v "$(pwd):/app" -w /app \
  -e ROS2_BRIDGE_TEST_DOMAIN=72 \
  -e CYCLONEDDS_URI=file:///etc/cyclonedds.xml \
  -e PYTHONPATH=/app \
  cyclonedds-python bash -lc "
    python3 -m pip install --quiet mcap pytest && \
    python3 -m pytest -q bridges/ros2_bridge/test_dds_roundtrip.py"
```

### Run Tier 3 (manual — requires a ROS 2 workspace)

You'll need ROS 2 (any of humble/iron/jazzy/rolling) with `sensor_msgs`,
`geometry_msgs`, `vision_msgs`, plus CycloneDDS Python (the same
`cyclonedds==11.0.1` the rest of this repo uses).

```bash
# 1. Verify the mocks match the installed ROS 2 IDL (one-time)
python3 bridges/ros2_bridge/verify_mocks.py

# 2. Save a config (see top of bridge_node.py for the schema)
cat > /tmp/bridge_config.yaml <<'EOF'
operator: operator_a
domain_id: 1
ros2_to_spatialdds:
  - ros2_topic: /robot/pose
    ros2_type: geometry_msgs/msg/PoseStamped
  - ros2_topic: /gps/fix
    ros2_type: sensor_msgs/msg/NavSatFix
    sensor_id: gnss_0
  - ros2_topic: /detections_3d
    ros2_type: vision_msgs/msg/Detection3DArray
spatialdds_to_ros2:
  - spatialdds_pattern: spatialdds/platform/fusion/track/v1
    ros2_topic: /fused/detections_3d
  - spatialdds_pattern: spatialdds/*/sensing/detection3d/v1
    ros2_topic_template: /{source_operator}/detections_3d
EOF

# 3. Start the bridge
python3 bridges/ros2_bridge/bridge_node.py \
    --ros-args -p config:=/tmp/bridge_config.yaml

# 4. In another terminal, publish a test PoseStamped
ros2 topic pub /robot/pose geometry_msgs/msg/PoseStamped \
  "{header: {frame_id: 'map'}, pose: {position: {x: 1.0, y: 2.0}}}" -r 1

# 5. Verify the SpatialDDS side received it
docker run --rm --network host \
  -v "$(pwd):/app" -w /app \
  -e CYCLONEDDS_URI=file:///etc/cyclonedds.xml -e PYTHONPATH=/app \
  cyclonedds-python python3 -m bridges.mcap_bridge.recorder /tmp/test.mcap \
    --domain 1 --topic 'spatialdds/operator_a/*' --duration 5
```

The reverse direction: run any SpatialDDS publisher (e.g. the multi-op
fusion demo) — the bridge will republish `fused_track` samples
as `vision_msgs/Detection3DArray` on `/fused/detections_3d`.

## Two-domain design

The bridge sits between two DDS domains:

- **ROS 2's domain** — set by `ROS_DOMAIN_ID`, populated automatically by
  `rclpy`. The robot's nodes talk on this.
- **SpatialDDS's domain** — set in the bridge's YAML config (`domain_id`),
  separate CycloneDDS participant. Other SpatialDDS publishers /
  subscribers / the MCAP bridge / the web bridge live here.

Keeping them split prevents type pollution: ROS 2 nodes don't see
SpatialDDS discovery noise, and SpatialDDS subscribers don't
see ROS 2 message types they can't parse.

## What the bridge adds that ROS 2 doesn't have

- **`source_operator` provenance** — every detection on the SpatialDDS bus
  carries the operator name, so multi-operator fusion can keep streams
  distinct.
- **FrameRef UUID stability** — ROS 2 tf2 frame IDs (`base_link`, `map`,
  `odom`) collide across robots. The bridge generates deterministic
  UUIDv5 IDs scoped by `(operator, frame_id)` so frames are globally
  unique on the SpatialDDS bus and reproducible across runs.
- **REP-145 sentinel preservation** — IMU samples without orientation
  (covariance[0] == −1) survive end-to-end as `has_orientation: false`,
  not silently coerced to identity quaternions.
- **Quaternion convention** — both ROS 2 and SpatialDDS use `(x, y, z, w)`,
  so no reordering. The bridge tests this explicitly so a future change
  to either side trips a test rather than a silent rotation bug.

## Sibling bridges

- [`bridges/web_bridge/`](../web_bridge/README.md) — HTTP-to-DDS bridge for the Cesium web UI.
- [`bridges/mcap_bridge/`](../mcap_bridge/README.md) — MCAP record/replay; the ROS 2 bridge
  reuses its lossless DDS reader/writer factories so QoS choices stay aligned.
