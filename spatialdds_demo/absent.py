"""
What a member 1.8 appended says when its publisher has nothing to say.

1.7 -> 1.8 changed eight types, and every change was an append: no member
removed, reordered or retyped. On the wire that is free -- an APPENDABLE
reader defaults what a 1.7 writer never sent. In Python it is not: the
generated dataclasses give no member a default, so a constructor written
against 1.7 raises ``TypeError: missing 4 required positional arguments``
the moment it is pointed at the 1.8 class.

So each appended group is stated once, here, as the value that means
*absent* in the spec's own terms, and splatted at construction sites:

    FrameRef(uuid=..., fqn=..., has_coord_convention=True,
             coord_convention=CoordConvention.ENU, **ABSENT_SCALE)

These are not neutral-looking zeros. Each is the value the spec defines as
"this publisher makes no such claim", and the docstrings say which clause.
"""
from __future__ import annotations

from spatialdds_demo import payloads as _payloads
from spatialdds.dds.types.spatial.common import ScaleStatus

#: FrameRef, appended in 1.8, typed. Derived from
#: ``payloads.ABSENT_SCALE`` -- the plain-data source -- rather than written
#: twice: see that constant for why ``has_scale=False`` restates 1.7 exactly.
#: A frame whose scale is known by survey should *declare* it instead
#: (``SCALE_DECLARED``, as the model layer's venue frame does).
ABSENT_SCALE = {
    **_payloads.ABSENT_SCALE,
    "scale_status": ScaleStatus[_payloads.ABSENT_SCALE["scale_status"]],
}


def _typed(cls, plain: dict) -> dict:
    """
    The typed values for ``plain``'s members of ``cls``, each built by the
    SDK's own N.1 lift -- so a typed constant here cannot disagree with what
    the codec builds from the plain-data source. Deriving rather than
    restating is the whole point: the plain form is the claim, this is only
    its spelling in Python objects.

    Member by member, because these are some of a type's members, not a
    whole record, and the SDK lifts only whole records. A struct or union
    member is lifted as its own record; an enum is looked up by name; the
    rest are plain already.
    """
    import enum
    import typing
    import spatialdds.dds as sdds
    hints = typing.get_type_hints(cls)
    out = {}
    for k, v in plain.items():
        hint = hints[k]
        target = getattr(hint, "__origin__", hint)
        if isinstance(target, type) and issubclass(target, enum.Enum):
            out[k] = target[v]
        elif isinstance(v, dict) and isinstance(target, type):
            out[k] = sdds.from_json(target, v)
        else:
            out[k] = v
    return out


def absent_detection3d() -> dict:
    """Detection3D's 1.8 Batch 2 members, typed; see payloads."""
    from spatialdds.dds.types.spatial.semantics import Detection3D
    return _typed(Detection3D, _payloads.absent_detection3d())


def absent_fused_track_observer() -> dict:
    """FusedTrack's 1.8 Batch 2 members, typed; see payloads."""
    from spatialdds.dds.types.spatial.semantics import FusedTrack
    return _typed(FusedTrack, _payloads.absent_fused_track_observer())


def absent_crossing() -> dict:
    """SpatialEvent's 1.8 crossing members, typed; see payloads."""
    from spatialdds.dds.types.spatial.events import SpatialEvent
    return _typed(SpatialEvent, _payloads.ABSENT_CROSSING)
