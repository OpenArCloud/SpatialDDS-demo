"""The crossing scene's names, frame and records: what every participant shares.

No SUMO here (world.py is the seam). This is the scene's half of what
`spatialdds_demo/lanes.py` and `model_service.py` are for the fountain:

* **The scene and its lanes.** Every lane is the SDK's registered name for the
  scene, or this repository's relationship extension on the same rule; the
  topic gate reads them from here (`LANES`).
* **The frame.** `map/crossing`: metres, ENU, declared. Its georeference
  is nominal and says so (`scenario.json`).
* **Entity ids.** One asserter, one id per thing it asserts:
  `ent:<asserter>:<thing>`. Two participants never write one key: a latched
  instance's history belongs to the writer that wrote it, so a shared key
  would be two records fighting over one instance (the fountain's
  ModelPublisher docstring, "Two writers, one instance").
* **Types**, as Wikidata items, each read from Wikidata on 2026-10-31.
* **`Publisher`**, the one way an asserter puts an entity on the bus: the
  latched record, refreshed on the SDK's recency rule, and the fast pose.
"""
from __future__ import annotations

import json
import math
import uuid
from pathlib import Path
from typing import Dict, Iterable, Optional, Sequence, Tuple

import spatialdds as sd
from spatialdds.dds.types.builtin import Time
from spatialdds.dds.types.spatial.common import (
    CoordConvention, FrameRef, KV, ScaleStatus)
from spatialdds.dds.types.spatial.core import (
    Aabb3, CovMatrix, FrameTransform, PoseSE3)
from spatialdds.dds.types.spatial.owm import (
    Basis, Entity, LifecycleState, ModelLayer, ModelPose)
from spatialdds.frames import EARTH_FIXED_FQN, enu_to_ecef_pose

from spatialdds_demo.lanes import (
    MODEL_FAST, MODEL_LATCHED, RELATIONSHIP, RELATIONSHIP_STREAM)

HERE = Path(__file__).resolve().parent
SCENARIO = json.loads((HERE / "scenario.json").read_text())

SCENE = SCENARIO["scene"]
FRAME_FQN = SCENARIO["frame_fqn"]

# -- lanes ----------------------------------------------------------------------

#: The scene's lanes, by what travels on them. Named here so that the topic
#: gate classifies them (tests/test_topic_registry.py).
ENTITY_TOPIC = sd.dds.topic_name(SCENE, None, "model_entity")
POSE_TOPIC = sd.dds.topic_name(SCENE, None, "model_pose")
RELATIONSHIP_TOPIC = sd.dds.topic_name(SCENE, RELATIONSHIP_STREAM, RELATIONSHIP)
#: As the fountain's model service opens it: stream "frames", a spec type
#: with no registered name.
FRAMES_TOPIC = sd.dds.topic_name(SCENE, "frames", "frame_transform")
LANES = {"model_entity": ENTITY_TOPIC, "model_pose": POSE_TOPIC,
         RELATIONSHIP: RELATIONSHIP_TOPIC, "frame_transform": FRAMES_TOPIC}

# -- types ----------------------------------------------------------------------

WD = "http://www.wikidata.org/entity/"
TYPE = {
    "car": WD + "Q1420",
    "bus": WD + "Q5638",
    "train": WD + "Q870",
    "pedestrian": WD + "Q221488",
    "building": WD + "Q41176",
    "bus_stop": WD + "Q953806",
    "level_crossing": WD + "Q171448",
    "crosswalk": WD + "Q8010",             # "pedestrian crossing"
    "lane": WD + "Q3222002",
    "rail": WD + "Q728937",                # "railway line"
    "gate": WD + "Q852010",                # "boom barrier"
    "roadside_unit": WD + "Q140775115",
}

# -- service ids and entity ids -------------------------------------------------


def service_id(name: str) -> str:
    """A participant's service id, which is also its asserter name."""
    return f"svc:crossing:{name}"


def entity_id(asserter: str, thing: str) -> str:
    """`ent:<asserter>:<thing>`: one asserter's record of one thing."""
    return f"ent:{asserter}:{thing}"


# -- the frame --------------------------------------------------------------------


def frame() -> FrameRef:
    """The scene frame, its uuid derived from its fqn as the fountain's is."""
    return FrameRef(
        uuid=str(uuid.uuid5(uuid.NAMESPACE_URL, FRAME_FQN)), fqn=FRAME_FQN,
        has_coord_convention=True, coord_convention=CoordConvention.ENU,
        has_scale=True, scale_status=ScaleStatus.SCALE_DECLARED,
        meters_per_unit=1.0, display_unit="m")


def earth_fixed() -> FrameRef:
    return FrameRef(
        uuid=str(uuid.uuid5(uuid.NAMESPACE_URL, EARTH_FIXED_FQN)),
        fqn=EARTH_FIXED_FQN, has_coord_convention=False,
        coord_convention=CoordConvention.ENU, has_scale=False,
        scale_status=ScaleStatus.SCALE_UNKNOWN, meters_per_unit=1.0,
        display_unit="m")


def georeference(stamp: Time) -> FrameTransform:
    """Where the scene frame sits on the earth: nominal (scenario.json).

    The same direction as the fountain's (`model_service.venue_georeference`):
    `parent_ref` is earth-fixed, `child_ref` the scene frame.
    """
    g = SCENARIO["georeference"]
    t, q = enu_to_ecef_pose(g["lat_deg"], g["lon_deg"], g["height_m"])
    return FrameTransform(
        transform_id=f"{EARTH_FIXED_FQN}->{FRAME_FQN}",
        parent_ref=earth_fixed(), child_ref=frame(),
        T_parent_child=PoseSE3(t=t, q=q), stamp=stamp, cov=CovMatrix(none=0))


# -- records ------------------------------------------------------------------------

ZERO_EXTENT = Aabb3(min_xyz=[0.0, 0.0, 0.0], max_xyz=[0.0, 0.0, 0.0])


def to_time(t: float) -> Time:
    ns = int(round(t * 1e9))
    sec, nanosec = divmod(ns, 1_000_000_000)
    return Time(sec=sec, nanosec=nanosec)


def yaw_quat(yaw_deg: float) -> list:
    """A rotation about +z (ENU up) as [x, y, z, w]."""
    h = math.radians(yaw_deg) / 2.0
    return [0.0, 0.0, math.sin(h), math.cos(h)]


def footprint_extent(x: float, y: float, z: float, yaw_deg: float,
                     length: float, width: float, height: float) -> Aabb3:
    """The scene-frame box around a rotated footprint: honestly inflated,
    since `Aabb3` is axis-aligned and the footprint is not. The true box is
    `oriented_dims` plus the pose's yaw."""
    c, s = math.cos(math.radians(yaw_deg)), math.sin(math.radians(yaw_deg))
    hx = abs(c) * length / 2.0 + abs(s) * width / 2.0
    hy = abs(s) * length / 2.0 + abs(c) * width / 2.0
    return Aabb3(min_xyz=[x - hx, y - hy, z], max_xyz=[x + hx, y + hy, z + height])


#: The oriented box's dimensions, as namespaced properties. 0.1's Entity has
#: no oriented extent (docs/crossing-census.md, the extent evidence), so the
#: true box travels as meta: these, centred on the pose and turned by its yaw.
OBB_KEYS = ("demo.obb.length_m", "demo.obb.width_m", "demo.obb.height_m")


def oriented_dims(length: float, width: float, height: float) -> list:
    """The oriented box's length (along the heading), width and height."""
    return [(k, f"{v:g}") for k, v in zip(OBB_KEYS, (length, width, height))]


def entity(eid: str, basis: Basis, kind: str, *, source: str, stamp: Time,
           layer: ModelLayer = ModelLayer.FAST,
           pose: Optional[Tuple[float, float, float, float]] = None,
           extent: Optional[Aabb3] = None,
           properties: Iterable[Tuple[str, str]] = (),
           state: LifecycleState = LifecycleState.ACTIVE,
           state_reason: str = "") -> Entity:
    """One Entity in the scene frame. `pose` is (x, y, z, yaw_deg)."""
    x, y, z, yaw = pose if pose is not None else (0.0, 0.0, 0.0, 0.0)
    return Entity(
        entity_id=eid, basis=basis, type_uris=[TYPE[kind]], layer=layer,
        frame_ref=frame(), has_pose=pose is not None,
        pose=PoseSE3(t=[x, y, z], q=yaw_quat(yaw)),
        has_extent=extent is not None, extent=extent or ZERO_EXTENT,
        properties=[KV(key=k, value=v) for k, v in properties],
        external_refs=[], content_refs=[], state=state,
        state_reason=state_reason, source_id=source, stamp=stamp)


class Publisher:
    """One asserter's entities: the latched record and the fast pose.

    The latched record is written when an entity is first asserted, when its
    properties or basis change, and on the SDK's recency rule (every
    `latch_every`-th pose, and once more when it goes still), as the
    fountain's model service does. A clean stop withdraws everything held
    (`on_exit="withdraw"`); a killed asserter withdraws nothing, and its
    readers see liveliness end.
    """

    def __init__(self, participant: "sd.dds.Participant", *,
                 latch_every: int = 10) -> None:
        self.source = participant.service_id
        self._entities = participant.writer(
            "model_entity", qos_profile=MODEL_LATCHED, on_exit="withdraw")
        self._poses = participant.writer("model_pose", qos_profile=MODEL_FAST)
        self._recency = sd.Recency(latch_every=latch_every)
        #: Each held entity as last asserted, latched or not.
        self._latest: Dict[str, Entity] = {}

    def held(self) -> Dict[str, Entity]:
        return dict(self._latest)

    def assert_entity(self, e: Entity, *, now: float, moving: bool) -> None:
        """Assert `e`. A moving entity's pose goes on the fast lane every
        time, and its latched record when the recency rule says; anything
        else that changed is latched at once."""
        prior = self._latest.get(e.entity_id)
        self._latest[e.entity_id] = e
        changed = (prior is None or prior.basis != e.basis
                   or prior.properties != e.properties
                   or prior.state != e.state)
        if moving:
            due = self._recency.observed(e.entity_id, now)
        else:
            due = prior is not None and prior.pose != e.pose
        if changed or due:
            self._entities.write(e)
        if moving:
            self._poses.write(ModelPose(entity_id=e.entity_id, pose=e.pose,
                                        source_id=self.source, stamp=e.stamp))

    def settle(self, now: float) -> None:
        """Latch, once, each entity that has gone still (the recency rule),
        with its latest pose."""
        for eid in self._recency.idle(now):
            e = self._latest.get(eid)
            if e is not None:
                self._entities.write(e)

    def withdraw(self, eid: str, reason: str) -> bool:
        """RETIRED with the reason, then the dispose. False if not held.

        The terminal sample's stamp is the SDK's, the wall clock: the pinned
        `withdraw()` sets it and refuses one passed in. Every other record of
        the scene is stamped on the simulation clock, so the two agree only
        at rate 1, which is the rate the scene is recorded at."""
        if eid not in self._latest:
            return False
        del self._latest[eid]
        self._recency.forget(eid)
        self._entities.withdraw(eid, reason=reason)
        return True


def kind_of(entity_or_types: Sequence[str]) -> str:
    """The scene's kind for a type URI list, or ''."""
    for k, uri in TYPE.items():
        if uri in entity_or_types:
            return k
    return ""
