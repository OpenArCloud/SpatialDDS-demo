"""
Latched world model, mirrored on the bridge — demo-local `oarc_model`.

The publisher already holds the model: both topics are TRANSIENT_LOCAL with
KEEP_LAST(1) per key, so DDS hands a late-joining *reader* the whole thing.
This cache exists because an HTTP client is not a reader. A browser asking
`GET /v1/model` has no DDS participant and no history to be replayed into, so
the bridge keeps the same latched view and serves it over HTTP.

That makes this a mirror, not a store. It holds no state the bus does not, and
a bridge restarted mid-session refills itself from the publisher's history
within a poll or two rather than starting empty.

Dispose evicts, and that is all it does in Part 1. Retirement is a
`LifecycleState` on the entity — RETIRED and UNOBSERVED are claims a consumer
may want to see — but tombstones and their flows are Part 2, and dispose
carries no payload to put one in.
"""

import threading
from typing import Any, Dict, List, Optional

from spatialdds_demo.json_mapping import to_json


class ModelCache:
    """Thread-safe, latest-wins-by-key, for entities and relationships."""

    def __init__(self) -> None:
        self._entities: Dict[str, Any] = {}
        self._relationships: Dict[str, Any] = {}
        # Instance handle -> key, so a dispose (which carries no payload) can
        # still be attributed to the thing that went away.
        self._entity_handles: Dict[int, str] = {}
        self._relationship_handles: Dict[int, str] = {}
        self._lock = threading.Lock()
        self.entity_updates = 0
        self.relationship_updates = 0
        self.evicted = 0

    # --- ingest ----------------------------------------------------------

    def admit_entity(self, entity: Any, instance_handle: Optional[int] = None) -> None:
        with self._lock:
            self._entities[entity.entity_id] = entity
            if instance_handle is not None:
                self._entity_handles[instance_handle] = entity.entity_id
            self.entity_updates += 1

    def admit_relationship(self, relationship: Any,
                           instance_handle: Optional[int] = None) -> None:
        with self._lock:
            self._relationships[relationship.rel_id] = relationship
            if instance_handle is not None:
                self._relationship_handles[instance_handle] = relationship.rel_id
            self.relationship_updates += 1

    def dispose_entity(self, instance_handle: Optional[int]) -> Optional[str]:
        with self._lock:
            key = self._entity_handles.pop(instance_handle or -1, None)
            if key and self._entities.pop(key, None) is not None:
                self.evicted += 1
                return key
        return None

    def dispose_relationship(self, instance_handle: Optional[int]) -> Optional[str]:
        with self._lock:
            key = self._relationship_handles.pop(instance_handle or -1, None)
            if key and self._relationships.pop(key, None) is not None:
                self.evicted += 1
                return key
        return None

    # --- read ------------------------------------------------------------

    def entities(self) -> List[Any]:
        with self._lock:
            return list(self._entities.values())

    def relationships(self) -> List[Any]:
        with self._lock:
            return list(self._relationships.values())

    def snapshot(self, stamp: Dict[str, int]) -> Dict[str, Any]:
        """
        The `/v1/model` body: the whole model, typed-in and JSON-out.

        Sorted by key so two calls with nothing in between are byte-identical
        — a diffable snapshot is worth more than insertion order here, and
        nothing downstream depends on arrival sequence.

        No pagination. The model is four entities; when it is not, this is the
        first thing that has to change, and the endpoint doc says so.
        """
        with self._lock:
            entities = [self._entities[k] for k in sorted(self._entities)]
            relationships = [self._relationships[k] for k in sorted(self._relationships)]
        return {
            "entities": [to_json(e) for e in entities],
            "relationships": [to_json(r) for r in relationships],
            "stamp": stamp,
        }

    def stats(self) -> Dict[str, int]:
        with self._lock:
            return {
                "entities": len(self._entities),
                "relationships": len(self._relationships),
                "entity_updates": self.entity_updates,
                "relationship_updates": self.relationship_updates,
                "evicted": self.evicted,
            }
