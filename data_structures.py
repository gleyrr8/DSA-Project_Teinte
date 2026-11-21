"""Small, explicit data structures used by Teinte's public and admin flows.

The database remains the durable source of truth. These structures are
request-scoped views used to order corrections and navigate catalogue/match
relationships safely in memory.
"""
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Iterable


class FIFOQueue:
    """First-in, first-out queue backed by deque."""

    def __init__(self, items: Iterable[Any] = ()) -> None:
        self._items = deque(items)

    def enqueue(self, item: Any) -> None:
        self._items.append(item)

    def dequeue(self) -> Any | None:
        return self._items.popleft() if self._items else None

    def __len__(self) -> int:
        return len(self._items)


class DirectedMatchGraph:
    """Adjacency-list graph of explicit source-shade -> target-shade claims."""

    def __init__(self) -> None:
        self._outgoing: dict[str, list[dict[str, Any]]] = {}

    def add_edge(self, source_id: str, target_id: str, claim: dict[str, Any]) -> None:
        if not source_id or not target_id:
            return  # Unresolved endpoints cannot become public match edges.
        self._outgoing.setdefault(str(source_id), []).append(claim)

    def outgoing(self, source_id: str) -> list[dict[str, Any]]:
        # Return a new list so callers cannot mutate the graph's adjacency list.
        return list(self._outgoing.get(str(source_id), ()))


@dataclass
class CatalogueNode:
    kind: str
    id: str
    label: str
    record: dict[str, Any] | None = None
    children: dict[str, "CatalogueNode"] = field(default_factory=dict)


def build_catalogue_tree(records: Iterable[dict[str, Any]]) -> CatalogueNode:
    """Build a brand -> product -> shade tree using stable IDs, not names."""
    root = CatalogueNode("root", "root", "Catalogue")
    for record in records:
        brand_id = str(record["brand_id"])
        product_id = str(record["product_id"])
        shade_id = str(record["shade_id"])
        brand = root.children.setdefault(
            brand_id,
            CatalogueNode("brand", brand_id, str(record["brand_name"])),
        )
        product = brand.children.setdefault(
            product_id,
            CatalogueNode("product", product_id, str(record["product_name"])),
        )
        # IDs make repeated shade/product names safe to represent.
        product.children.setdefault(
            shade_id,
            CatalogueNode("shade", shade_id, str(record["shade_name"]), record),
        )
    return root


def catalogue_node_to_dict(node: CatalogueNode) -> dict[str, Any]:
    """Recursively serialize the catalogue tree for the public browse UI."""
    result: dict[str, Any] = {
        "kind": node.kind,
        "id": node.id,
        "label": node.label,
        "children": [catalogue_node_to_dict(child) for child in node.children.values()],
    }
    if node.record is not None:
        result["shade"] = node.record
    return result
