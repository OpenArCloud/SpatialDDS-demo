"""
Catalogue query semantics, in one place.

**Demo-local, not a spec surface.** The catalogue protocol (`oarc_demo`) is
specific to this repo; `CatalogFilter` is not `spatial::disco`'s
`CoverageFilter`, which filters on `type_in` / `qos_profile_in` /
`module_id_in`. The two are written in the same `has_filter` + `*_in` style so
one vocabulary covers both query surfaces, and that is the whole relationship.

This module exists because the matcher was implemented twice -- once in the AR
demo's catalogue server, once in the conformance harness -- in the same
language, with no reason for the copies beyond how they grew. Two servers that
answer the same query differently is a worse failure than one that answers it
wrongly, because only one of those gets noticed. Both now call this.
"""

from typing import Any, Dict


def matches_catalog_filter(entry: Dict[str, Any], query: Dict[str, Any]) -> bool:
    """
    Whether one catalogue row satisfies a query's filter.

    An empty list in either lane means "match all" in that lane, mirroring the
    discovery filter's empty-array semantics, and the lanes intersect.

    `content_id_in` is lookup-by-id. Before it the catalogue could answer
    "what is near here" and not "what is this id", so a `catalog:<content_id>`
    reference was resolvable only by a client that had already queried the
    right area -- reference-by-id existed and lookup-by-id did not. See
    SPEC_COMPLIANCE.
    """
    if not query.get("has_filter"):
        return True

    filters = query.get("filter") or {}

    # An id list, when given, is the narrowest thing in the query: it names
    # exactly the rows the caller wants. It intersects with kind_in rather
    # than overriding it -- an id list is not a way around a kind filter.
    ids = filters.get("content_id_in") or []
    if ids and entry.get("content_id") not in ids:
        return False

    kinds = filters.get("kind_in") or []
    if kinds and entry.get("kind") not in kinds:
        return False
    return True
