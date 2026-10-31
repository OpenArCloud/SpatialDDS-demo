#!/usr/bin/env python3
"""The map author: the scene frame's georeference, its zones and its buildings.

    python3 -m scenarios.crossing.map_author --domain 47 --port 8871

AUTHORED, every record: the zones and buildings are the scenario's, written
by hand in `scenario.json`, not observed by anyone. This is the basis the
fountain never had. Asserted once at time 0 and held until the session
ends. The georeference goes first, so no entity names a frame its scene has
not placed.
"""
from __future__ import annotations

import spatialdds as sd
from spatialdds.dds.types.spatial.core import Aabb3, FrameTransform
from spatialdds.dds.types.spatial.owm import Basis, ModelLayer

from spatialdds_demo.lanes import MODEL_LATCHED
from scenarios.crossing import participant as runner
from scenarios.crossing.scene import (
    SCENARIO, Publisher, entity, entity_id, georeference)

NAME = "map"


class MapAuthor:
    def __init__(self, p: "sd.dds.Participant") -> None:
        # Latched and kept on a clean stop, as the fountain's georeference is:
        # where the frame is does not stop being true when its author leaves.
        self._frames = p.writer(FrameTransform, type_name="frame_transform",
                                stream="frames", qos_profile=MODEL_LATCHED,
                                on_exit="keep")
        self.pub = Publisher(p)

    def records(self, stamp):
        for z in SCENARIO["zones"]:
            yield entity(
                entity_id(NAME, z["id"]), Basis.AUTHORED, z["type"],
                source=self.pub.source, stamp=stamp, layer=ModelLayer.STATIC,
                pose=(*[(a + b) / 2 for a, b in zip(z["min_xyz"], z["max_xyz"])], 0.0),
                extent=Aabb3(min_xyz=list(z["min_xyz"]), max_xyz=list(z["max_xyz"])),
                properties=[("demo.label", z["label"])])
        for b in SCENARIO["buildings"]:
            yield entity(
                entity_id(NAME, b["id"]), Basis.AUTHORED, "building",
                source=self.pub.source, stamp=stamp, layer=ModelLayer.STATIC,
                pose=(*[(a + c) / 2 for a, c in zip(b["min_xyz"], b["max_xyz"])], 0.0),
                extent=Aabb3(min_xyz=list(b["min_xyz"]), max_xyz=list(b["max_xyz"])),
                properties=[("demo.label", "Building"),
                            ("demo.height_m", f"{b['max_xyz'][2]:g}")])

    def on_start(self, stamp) -> None:
        self._frames.write(georeference(stamp))
        n = 0
        for e in self.records(stamp):
            self.pub.assert_entity(e, now=0.0, moving=False)
            n += 1
        print(f"map: georeference and {n} authored entities", flush=True)

    def on_truth(self, frame, stamp) -> None:
        pass


if __name__ == "__main__":
    runner.main_for(NAME, MapAuthor, __doc__)
