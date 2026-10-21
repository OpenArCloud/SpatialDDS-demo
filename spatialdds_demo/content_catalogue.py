"""
The demo's content catalogue in 1.8's shape: a thin announce, a rich manifest.

1.8 says it plainly: "the catalogue (manifests, ContentAnnounce) says what
content *exists* and where to fetch it". So each catalogue row becomes two
documents, and this module is the one place both are built, from the same
row, so they cannot disagree:

* a latched ``spatial::disco::ContentAnnounce`` on
  ``spatialdds/discovery/content/v1`` -- the inventory, on the bus, where a
  headless DDS client reads it with no HTTP at all;
* a ``spatial.manifest/1.8`` document of ``rtype: content``, named by the
  announce's ``manifest_uri`` and served by the resolver (spec §7.5) -- the
  assets, their media types and their hashes.

It replaces the demo's 1.7 catalogue protocol (``oarc_demo::CatalogQuery`` /
``CatalogEntry`` / ``CatalogResponse``), whose job the spec now does.
``docs/final-migration-census.md`` §1 has the field-by-field mapping and
``tests/test_catalogue_chain.py`` asserts it against a frozen capture of what
the old protocol returned.

**What 1.8 carries natively, and what it does not.** Placement rides a
``Transform`` from the content's own frame into the frame it is placed in --
the slot ``ContentAnnounce`` already has, and on the bus. A hashed asset rides
``assets[]``. Three things have no conformant carrier, and they go in a single
labelled demo extension, ``"oarc.catalog"``, which §8.1 permits because
unknown top-level manifest fields MUST be ignored:

* an ``href`` with no hash -- 1.8's ``AssetRef`` requires ``hash``, and 32 of
  the demo's 34 seed rows advertise placeholder content that nobody can hash;
* ``formats`` that are not media types (``"gltf"``, ``"3dtiles"``);
* the ``frame_ref`` of a row that is not placed in it, which no transform can
  state without asserting a pose the row does not have.

Each is a gap worth knowing about rather than a demo convenience; they are
filed with the query ergonomics in ``docs/owm-1.8-spec-queue.md``.
"""
from __future__ import annotations

import copy
import json
import os
import re
import uuid as _uuid
from typing import Any, Dict, List, Optional, Tuple

from spatialdds_demo.catalog_semantics import matches_catalog_filter

from spatialdds_demo.discovery_http import MANIFEST_PROFILE
from spatialdds_demo.payloads import ABSENT_SCALE
from spatialdds_validation import complete_coverage_element

#: Where the demo's content URIs live. Configurable because the authority is
#: a deployment fact; the resolver metadata must name the same one.
DEFAULT_AUTHORITY = "catalog.example.com"
DEFAULT_ZONE = "demo"

#: The labelled demo extension. One key, so a reader can see at a glance
#: everything this manifest says that 1.8 has no field for.
EXTENSION_KEY = "oarc.catalog"

_RID = re.compile(r"^[A-Za-z0-9_-]+$")


def authority() -> str:
    return os.getenv("SPATIALDDS_CONTENT_AUTHORITY", DEFAULT_AUTHORITY)


def zone() -> str:
    return os.getenv("SPATIALDDS_CONTENT_ZONE", DEFAULT_ZONE)


# --- the seed: what a human authors, completed to what the wire carries ----
#
# Moved here from ar_demo/spatialdds_catalog_server.py so the catalogue
# service (which announces) and the bridge's resolver (which serves manifests)
# complete rows with one function rather than two that could drift.


def _complete_entry(entry: Dict[str, Any]) -> Dict[str, Any]:
    """
    Fill the presence-flagged members a hand-authored row omits.

    Same bargain as `complete_coverage_element`: the seed carries what a human
    cares about, and the builder supplies the rest so a row that predates a
    field still types. `pose` and `asset` were appended after the 1.7 review of
    this response, and the tower seed does not carry either.
    """
    entry.setdefault("has_pose", False)
    entry.setdefault("pose", {"t": [0.0, 0.0, 0.0], "q": [0.0, 0.0, 0.0, 1.0]})
    entry.setdefault("has_asset", False)
    entry.setdefault("asset", {"uri": "", "media_type": "", "hash": "", "meta": []})
    return entry


def _absolutise(entry: Dict[str, Any], base: str) -> Dict[str, Any]:
    """
    Resolve the authored asset URI against the publisher's configured base.

    `href` is relative and names no base, so it means one thing to a client
    that reached the catalogue through the web bridge and another to one
    talking to this service -- the ambiguity the spec's manifests avoid by
    being absolute. The seed stays relative because that is what is portable
    between deployments; the base is deployment configuration
    (`SPATIALDDS_ASSET_BASE`), and what goes on the wire is absolute.

    With no base configured the URI is left as authored, which is what the
    local demo wants: there the page and the asset share an origin.
    """
    asset = entry.get("asset") or {}
    uri = asset.get("uri", "")
    if base and uri and not uri.startswith(("http://", "https://", "spatialdds://")):
        asset["uri"] = f"{base.rstrip('/')}/{uri.lstrip('/')}"
    return entry


def load_seed(path: str) -> List[Dict[str, Any]]:
    """
    Load the authored catalogue and complete its coverage elements.

    `catalog_seed.json` is hand-authored: it carries the fields a human cares
    about and omits the presence-flagged ones that are always on the wire.
    That is the right shape for authored data — it should not have to track
    every field the IDL gains. `complete_coverage_element` fills the rest in,
    so the seed does not silently stop building when the spec adds one, which
    is exactly what happened when 1.7 added `has_circle`/`circle_center`/
    `circle_radius_m` and this file was three revisions older than the IDL.
    """
    with open(path, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, list):
        raise ValueError("catalog_seed.json must be a list")
    base = os.getenv("SPATIALDDS_ASSET_BASE", "")
    for entry in payload:
        entry["coverage"] = [
            complete_coverage_element(**element)
            for element in (entry.get("coverage") or [])
        ]
        _absolutise(_complete_entry(entry), base)
    return payload


def content_rid(content_id: str) -> str:
    """
    The Appendix F ``rid`` for a content id.

    Used as-is when it already conforms. The AR demo's ids do not --
    ``mesh/sf-downtown/item-000`` carries ``/``, which ``rid-char`` excludes --
    so those map to a deterministic UUIDv5 rather than a character
    substitution, which could collide (``a/b`` and ``a_b`` would be one
    rid). The id itself is unchanged everywhere it appears as an id.
    """
    if _RID.match(content_id):
        return content_id
    return "u-" + _uuid.uuid5(_uuid.NAMESPACE_URL, f"content:{content_id}").hex


def content_uri(content_id: str) -> str:
    return f"spatialdds://{authority()}/{zone()}/content/{content_rid(content_id)}"


def _frame(fqn: str, coord_convention: str = "ENU",
           has_coord_convention: bool = True) -> Dict[str, Any]:
    return {"uuid": str(_uuid.uuid5(_uuid.NAMESPACE_URL, fqn)), "fqn": fqn,
            "has_coord_convention": has_coord_convention,
            "coord_convention": coord_convention, **ABSENT_SCALE}


def _as_18_frame(frame_ref: Dict[str, Any]) -> Dict[str, Any]:
    """A seed row's FrameRef, completed with 1.8's appended scale members."""
    return {**ABSENT_SCALE, **copy.deepcopy(frame_ref)}


def content_frame_fqn(row: Dict[str, Any]) -> str:
    """The content's own frame: the one its placement maps *from*."""
    parent = (row.get("frame_ref") or {}).get("fqn", "")
    return f"{parent}/content/{content_rid(row['content_id'])}"


def placement(row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Where a placed row sits, as a ``spatial::disco::Transform``.

    Per the struct: the pose maps coordinates *from* ``from`` *into* ``to`` --
    here from the content's own frame into the frame the row names, which is
    exactly what the 1.7 row's ``pose`` meant ("where the content sits inside
    frame_ref, in that frame's metres"). None for a row with no pose: a
    Transform has no presence flag, and an identity pose would claim the
    content sits at the frame origin.
    """
    if not row.get("has_pose"):
        return None
    pose = row["pose"]
    return {
        "from": _frame(content_frame_fqn(row)),
        "to": _as_18_frame(row["frame_ref"]),
        "pose": {"t": list(pose["t"]), "q": list(pose["q"])},
        "stamp": {"sec": int(row.get("updated_sec") or 0), "nanosec": 0},
        # Content is not going anywhere; an unbounded placement is the honest
        # statement, as for the frame anchors the catalogue already announces.
        "has_validity": False,
        "validity": {"from": {"sec": 0, "nanosec": 0}, "seconds": 0},
    }


def _hashed_assets(row: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The row's asset as a 1.8 ``AssetRef`` -- only when it carries a hash."""
    asset = row.get("asset") or {}
    if not (row.get("has_asset") and asset.get("uri") and asset.get("hash")):
        return []
    return [{"uri": asset["uri"], "media_type": asset.get("media_type", ""),
             "hash": asset["hash"], "meta": list(asset.get("meta") or [])}]


def content_announce(row: Dict[str, Any], *, provider_id: str,
                     frame_anchor: Optional[Dict[str, Any]] = None,
                     ttl_sec: int = 300,
                     stamp: Optional[Dict[str, int]] = None) -> Dict[str, Any]:
    """
    One row as a ``ContentAnnounce`` -- the thin, latched half.

    ``frame_anchor`` is the placed frame's own transform into earth-fixed,
    when the deployment has one. Carried here as well as on the service's
    Announce so a client holding only this sample can place the content.

    **``stamp`` is when this announce was published**, and it pairs with
    ``ttl_sec``: a consumer treats the announce as stale after
    ``stamp + ttl_sec``. The row's ``updated_sec`` -- when the *content* last
    changed -- is a different fact and lives on the manifest's ``stamp``. The
    first cut of this put it here, which made a row last edited in January
    announce itself as having expired in January. The publisher republishes
    well inside the TTL.
    """
    if stamp is None:
        import time as _time
        now = _time.time()
        stamp = {"sec": int(now), "nanosec": int((now % 1) * 1e9)}
    transforms = [t for t in (placement(row), frame_anchor) if t]
    return {
        "content_id": row["content_id"],
        "provider_id": provider_id,
        "title": row.get("name", ""),
        "summary": "",
        "tags": [],
        "class_id": row.get("kind", ""),
        "manifest_uri": content_uri(row["content_id"]),
        "coverage": copy.deepcopy(row.get("coverage") or []),
        # Coverage elements are geographic (EPSG:4979); their frame is
        # earth-fixed, and has no axis convention CoordConvention can name.
        "coverage_frame_ref": _frame("earth-fixed", has_coord_convention=False),
        "has_coverage_eval_time": False,
        "coverage_eval_time": {"sec": 0, "nanosec": 0},
        "transforms": transforms,
        "available_from": {"sec": 0, "nanosec": 0},
        "available_until": {"sec": 0, "nanosec": 0},
        "stamp": dict(stamp),
        "ttl_sec": int(ttl_sec),
    }


def content_manifest(row: Dict[str, Any]) -> Dict[str, Any]:
    """
    One row as a ``spatial.manifest/1.8`` content manifest -- the rich half.

    ``stamp`` is the row's ``updated_sec``: a manifest is the document that
    changes when the content does, so its publication time is the content's
    update time. ``stamp.sec`` is a decimal string, because a manifest is JSON
    produced by the spec's bindings and follows N.1 (an int64 is a string).

    No ``ttl_sec``: §7.5.2 makes the resolver's ``cache_ttl_sec`` the default
    for a manifest that carries none, counted from when it was fetched. A
    ``ttl_sec`` here would be counted from ``stamp`` -- the content's update
    time -- and declare an unchanged manifest stale the moment it was served.
    """
    manifest: Dict[str, Any] = {
        "id": content_uri(row["content_id"]),
        "profile": MANIFEST_PROFILE,
        "rtype": "content",
        "content": {
            "content_id": row["content_id"],
            "title": row.get("name", ""),
            "class_id": row.get("kind", ""),
        },
        "coverage": {"elements": copy.deepcopy(row.get("coverage") or [])},
        "assets": _hashed_assets(row),
        "stamp": {"sec": str(int(row.get("updated_sec") or 0)), "nanosec": 0},
    }
    place = placement(row)
    if place:
        # Not in §8.1's table, but the spec's own content example carries a
        # top-level `transforms`, and unknown top-level fields are ignored.
        manifest["transforms"] = [place]
    extension: Dict[str, Any] = {}
    if not manifest["assets"]:
        if row.get("href"):
            extension["href"] = row["href"]
        if row.get("formats"):
            extension["formats"] = list(row["formats"])
    if not place and row.get("frame_ref"):
        extension["frame_ref"] = _as_18_frame(row["frame_ref"])
    if extension:
        manifest[EXTENSION_KEY] = extension
    return manifest


def manifest_etag(manifest: Dict[str, Any]) -> str:
    """§7.5.2's integrity signal: a strong ETag over the canonical body."""
    import hashlib
    body = json.dumps(manifest, sort_keys=True, separators=(",", ":"))
    return '"sha256:' + hashlib.sha256(body.encode()).hexdigest() + '"'


#: How far either side of a pose an area query reaches, in degrees -- the box
#: the bridge built for the 1.7 catalogue query, kept so the area means what
#: it meant.
AREA_PADDING_DEG = 0.005


def filter_inventory(items: List[Dict[str, Any]], *,
                     geopose: Optional[Dict[str, Any]] = None,
                     kind_in: Optional[List[str]] = None,
                     content_id_in: Optional[List[str]] = None
                     ) -> List[Dict[str, Any]]:
    """
    The announced items a reader asked for -- reader-side filtering.

    1.8 has no content query (`CoverageQuery` answers with *services*), so a
    consumer filters the inventory it reads. That is fine at this scale and
    is what the 1.9 entry on query ergonomics is about at larger ones.

    One matcher decides kind and id: ``catalog_semantics``, adapted here to
    an announce (whose kind is ``class_id``) rather than copied. Area is the
    coverage intersection the 1.7 server applied, except for a lookup by id,
    which is not a lookup by place: a client holding ``catalog:<id>`` may
    know nothing about where the thing is.
    """
    from spatialdds_validation import (
        SpatialDDSValidator, create_coverage_bbox_earth_fixed)
    kinds, ids = list(kind_in or []), list(content_id_in or [])
    query = {"has_filter": bool(kinds or ids),
             "filter": {"kind_in": kinds, "content_id_in": ids}}
    area = None
    if geopose and not ids:
        lon, lat = float(geopose["lon_deg"]), float(geopose["lat_deg"])
        _, element = create_coverage_bbox_earth_fixed(
            lon - AREA_PADDING_DEG, lat - AREA_PADDING_DEG,
            lon + AREA_PADDING_DEG, lat + AREA_PADDING_DEG)
        area = [element]
    out = []
    for item in items:
        if not matches_catalog_filter(
                {"content_id": item.get("content_id"), "kind": item.get("class_id")},
                query):
            continue
        if area and item.get("coverage") and not \
                SpatialDDSValidator.check_coverage_intersection(area, item["coverage"]):
            continue
        out.append(item)
    return sorted(out, key=lambda i: i.get("content_id") or "")


#: How long a fetched manifest stays good when it carries no ``ttl_sec`` of
#: its own, per §7.5.2 -- advertised in the resolver metadata.
RESOLVER_CACHE_TTL_SEC = 300


def resolver_metadata(https_base: str) -> Dict[str, Any]:
    """The body of ``/.well-known/spatialdds/resolver`` (§7.5.2)."""
    return {"authority": authority(), "https_base": https_base,
            "cache_ttl_sec": RESOLVER_CACHE_TTL_SEC}


def resolve(uri: str, rows: List[Dict[str, Any]]
            ) -> Tuple[int, Optional[Dict[str, Any]], str]:
    """
    ``GET {https_base}?uri=`` for this authority: (status, manifest, etag).

    §7.5.3's codes: 400 for a URI that is not a SpatialDDS URI at all, 404 for
    one that is well formed but names nothing here -- another authority, a
    resource type this catalogue does not hold, or an id it does not have.
    """
    from spatialdds_validation import SpatialDDSValidator
    try:
        parts = SpatialDDSValidator.validate_spatial_uri(uri)
    except Exception:
        return 400, None, ""
    if parts["authority"] != authority() or parts["rtype"] != "content":
        return 404, None, ""
    for row in rows:
        if content_uri(row["content_id"]) == uri:
            manifest = content_manifest(row)
            return 200, manifest, manifest_etag(manifest)
    return 404, None, ""
