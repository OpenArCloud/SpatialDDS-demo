"""
JSON schemas for MCAP: the published N.1 schemas the SDK ships.

MCAP wants a schema per message type. Under the envelope there was nothing to
derive one from, so every channel got a permissive `{"type": "object"}`.
Typed topics made the schema a fact, and this file used to generate one from
each generated dataclass, in this repository's own JSON dialect.

**Since the SDK adoption the payload is N.1, and so is the schema.** The
recorder writes `sd.dds.to_json` of each sample, the gateway's own
projection. So the schema that describes it is the one the gateway publishes
for that type (`spatialdds.server.webjson.schemas()`, at the SDK's `v1.8`
pin), not one generated here. There are not two descriptions of one payload
to drift apart. A recording made before the adoption carries the old
dialect's schemas. It describes its own payloads correctly, and is an
artifact of that dialect.

A deployment extension (`oarc.*`) has no published schema. It gets the
permissive one, titled with its name, and so does a type this build cannot
resolve at all: a recorder must never refuse a stream because it has never
heard of its type.
"""

from __future__ import annotations

from typing import Dict, Iterable, Optional


def default_schema(type_name: str) -> dict:
    """A permissive schema, for a type with no published N.1 schema."""
    return {
        "title": type_name,
        "type": "object",
        "additionalProperties": True,
    }


def schema_for(type_name: str) -> dict:
    """The published N.1 schema for a registered type name, or the permissive one."""
    import spatialdds.dds as sdds
    from spatialdds.server import webjson
    from spatialdds_demo import lanes
    try:
        fqn = sdds.fqn_of(lanes.class_for(type_name))
    except Exception:
        return default_schema(type_name)
    schema = webjson.schemas().get(fqn)
    if schema is None:
        return default_schema(type_name)
    return dict(schema, title=type_name)


def build_schema_table(overrides: Optional[Dict[str, dict]] = None,
                       names: Optional[Iterable[str]] = None) -> Dict[str, dict]:
    """
    `{type_name: schema}` for every registered type and this repository's
    extensions, plus any overrides.

    `names` restricts the table. By default it covers every name the registry
    and `spatialdds_demo.lanes` know (its extensions, and its names for pinned
    types the registry does not register), so a recorder can register a channel for
    any stream that turns up without being told about it in advance.
    """
    if names is None:
        from spatialdds.dds import registry
        from spatialdds_demo import lanes
        names = sorted(set(registry.registrations()) | set(lanes.EXTENSIONS)
                       | set(lanes.DEPLOYMENT_NAMES))
    table = {name: schema_for(name) for name in names}
    if overrides:
        table.update(overrides)
    return table
