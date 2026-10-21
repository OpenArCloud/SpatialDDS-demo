"""
Catalogue filter semantics, in one place.

**Demo-local, not a spec surface.** 1.8 has no content query -- the inventory
is a latched `ContentAnnounce` topic and a reader filters what it holds -- so
the `kind_in` / `content_id_in` filter here is the demo's reader-side rule,
applied by `content_catalogue.filter_inventory` for every consumer: the
bridge's `/v1/content/query`, the headless AR client, the walkthrough. It is
not `spatial::disco`'s `CoverageFilter` (`type_in` / `qos_profile_in` /
`module_id_in`); the two share the `has_filter` + `*_in` style and nothing
else.

It began as the 1.7 catalogue protocol's matcher, implemented twice -- in the
catalogue server and in the conformance harness -- with no reason for the
copies beyond how they grew. Two answers to the same query is a worse failure
than one wrong answer, because only one of those gets noticed. The protocol
retired in the 1.8 migration; the one-copy rule did not.
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
