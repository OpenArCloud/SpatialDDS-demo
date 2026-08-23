"""
JSON schemas for MCAP, derived from the SpatialDDS IDL.

MCAP wants a schema per message type. Under the envelope there was nothing
to derive one from — every payload was an opaque JSON string whose shape
lived in whichever publisher wrote it — so this file listed the demo's
``msg_type`` labels and gave each a permissive ``{"type": "object"}``. A
recording said what its messages were called and nothing about what was in
them.

Typed topics make the schema a fact rather than a guess: the generated
dataclass for a §3.3.2 type has fields, and those fields have types. So the
schema is generated from the class, and a recording carries the real shape
of every message in it — which is what makes an MCAP file readable by
someone who does not have this repo.

The mapping is the JSON mapping in ``spatialdds_demo.json_mapping``, so what
the schema describes is exactly what the recorder writes: enums as their
identifier strings (§2.8), IDL field names, bytes as base64, and
presence-flagged optionals present with their flag beside them.
"""

from __future__ import annotations

import base64
import dataclasses
import enum
from typing import Any, Dict, Iterable, Optional, get_args, get_origin


def default_schema(type_name: str) -> dict:
    """A permissive schema, for a type this build cannot resolve."""
    return {
        "title": type_name,
        "type": "object",
        "additionalProperties": True,
    }


def schema_for(type_name: str) -> dict:
    """
    A JSON schema for one registered §3.3.2 type, generated from its IDL.

    Falls back to the permissive schema when the name resolves to nothing —
    a recorder must never refuse to record a stream just because this build
    has never heard of its type.
    """
    try:
        from spatialdds_demo import topic_types

        datatype = topic_types.try_resolve(type_name)
    except Exception:
        datatype = None
    if datatype is None:
        return default_schema(type_name)
    schema = _struct_schema(datatype, set())
    schema["title"] = type_name
    return schema


def build_schema_table(overrides: Optional[Dict[str, dict]] = None,
                       names: Optional[Iterable[str]] = None) -> Dict[str, dict]:
    """
    ``{type_name: schema}`` for every registered type, plus any overrides.

    ``names`` restricts the table; by default it covers everything the
    registry knows, so a recorder can register a channel for any stream that
    turns up without being told about it in advance.
    """
    if names is None:
        try:
            from spatialdds_demo import topic_types

            names = sorted(topic_types.ALL)
        except Exception:
            names = ()
    table = {name: schema_for(name) for name in names}
    if overrides:
        table.update(overrides)
    return table


# --- IDL -> JSON schema -----------------------------------------------------

_PRIMITIVES = {
    bool: {"type": "boolean"},
    int: {"type": "integer"},
    float: {"type": "number"},
    str: {"type": "string"},
    bytes: {"type": "string", "contentEncoding": "base64"},
    bytearray: {"type": "string", "contentEncoding": "base64"},
}


def _struct_schema(cls: Any, seen: set) -> dict:
    """One IDL struct as a JSON-schema object."""
    if cls in seen:
        # Recursive types would otherwise not terminate. None of 1.7's do,
        # but a generated schema should not depend on that staying true.
        return {"type": "object"}
    seen = seen | {cls}

    hints = _hints(cls)
    aliases = _field_aliases(cls)
    properties: Dict[str, dict] = {}
    required = []
    for field in dataclasses.fields(cls):
        wire = aliases.get(field.name, field.name)
        properties[wire] = _schema_for_hint(hints.get(field.name), seen)
        # Every field is required. The spec has no absent fields: optionals
        # are a has_x flag beside a value that is always written.
        required.append(wire)
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


# IDL integer widths, so a schema says "uint8" rather than just "integer".
_INT_BOUNDS = {
    "int8": (-128, 127), "uint8": (0, 255),
    "int16": (-32768, 32767), "uint16": (0, 65535),
    "int32": (-2147483648, 2147483647), "uint32": (0, 4294967295),
    "int64": (-(2 ** 63), 2 ** 63 - 1), "uint64": (0, 2 ** 64 - 1),
}


def _annotated_schema(hint: Any) -> Optional[dict]:
    """``Annotated[float, 'float64']`` and friends — idlc's primitive form."""
    args = get_args(hint)
    if not args or get_origin(hint) is not None and not hasattr(hint, "__metadata__"):
        return None
    if not hasattr(hint, "__metadata__"):
        return None
    base, width = args[0], str(args[1]) if len(args) > 1 else ""
    if base is float:
        return {"type": "number", "format": width or "double"}
    if base is int:
        schema = {"type": "integer", "format": width}
        bounds = _INT_BOUNDS.get(width)
        if bounds:
            schema["minimum"], schema["maximum"] = bounds
        return schema
    if base in _PRIMITIVES:
        return dict(_PRIMITIVES[base])
    return None


def _schema_for_hint(hint: Any, seen: set) -> dict:
    if hint is None:
        return {}
    hint = _unwrap(hint)

    annotated = _annotated_schema(hint)
    if annotated is not None:
        return annotated

    if isinstance(hint, type):
        if hint in _PRIMITIVES:
            return dict(_PRIMITIVES[hint])
        if issubclass(hint, enum.Enum):
            # §2.8: identifiers on the wire, not the integer values.
            return {"type": "string", "enum": [m.name for m in hint]}
        if dataclasses.is_dataclass(hint):
            return _struct_schema(hint, seen)
        if _is_union_type(hint):
            return _union_schema(hint, seen)

    origin = get_origin(hint)
    if origin in (list, tuple):
        args = get_args(hint) or ()
        return {"type": "array",
                "items": _schema_for_hint(args[0] if args else None, seen)}

    # sequence[X, n] / array[X, n]: the element type is the subtype, and an
    # array carries a fixed length worth asserting.
    subtype = getattr(hint, "subtype", None)
    if subtype is not None:
        schema = {"type": "array", "items": _schema_for_hint(subtype, seen)}
        length = getattr(hint, "length", None)
        if length and type(hint).__name__ == "array":
            schema["minItems"] = schema["maxItems"] = int(length)
        elif length:
            schema["maxItems"] = int(length)
        return schema

    if isinstance(hint, str):
        return {}                        # unresolved forward ref
    return {}


def _union_schema(cls: Any, seen: set) -> dict:
    """An IDL union: a discriminator plus whichever case is active."""
    properties = {"discriminator": {"type": "string"}}
    for member, hint in _hints(cls).items():
        properties[member] = _schema_for_hint(getattr(hint, "subtype", hint), seen)
    return {
        "type": "object",
        "properties": properties,
        "required": ["discriminator"],
        "additionalProperties": False,
    }


def _is_union_type(target: Any) -> bool:
    try:
        import cyclonedds.idl as idl

        return isinstance(target, type) and issubclass(target, idl.IdlUnion)
    except Exception:
        return False


def _unwrap(target: Any) -> Any:
    """Strip cyclonedds typedef wrappers, but keep sequence/array wrappers."""
    seen = 0
    while (type(target).__name__ == "typedef"
           and hasattr(target, "subtype") and seen < 8):
        target = target.subtype
        seen += 1
    return target


def _hints(cls: Any) -> Dict[str, Any]:
    try:
        from cyclonedds.idl._type_normalize import get_extended_type_hints

        return dict(get_extended_type_hints(cls))
    except Exception:
        return dict(getattr(cls, "__annotations__", {}) or {})


def _field_aliases(cls: Any) -> Dict[str, str]:
    """idlc renames Python keywords; the aliases put the IDL name back."""
    try:
        from spatialdds_idl._field_aliases import FIELD_ALIASES

        idl = getattr(cls, "__idl__", None)
        name = getattr(idl, "idl_transformed_typename", "") or ""
        return FIELD_ALIASES.get(name.replace("::", "."), {})
    except Exception:
        return {}
