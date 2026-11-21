import unittest

from data_structures import (
    FIFOQueue,
    DirectedMatchGraph,
    build_catalogue_tree,
    catalogue_node_to_dict,
)


class DataStructureTests(unittest.TestCase):
    def test_fifo_queue_returns_items_in_arrival_order_and_handles_empty(self):
        queue = FIFOQueue()
        self.assertIsNone(queue.dequeue())
        queue.enqueue("first")
        queue.enqueue("second")
        self.assertEqual(queue.dequeue(), "first")
        self.assertEqual(queue.dequeue(), "second")
        self.assertIsNone(queue.dequeue())

    def test_directed_graph_returns_only_outgoing_edges(self):
        graph = DirectedMatchGraph()
        ab = {"shade_id": "B", "claim": "A to B"}
        bc = {"shade_id": "C", "claim": "B to C"}
        graph.add_edge("A", "B", ab)
        graph.add_edge("B", "C", bc)
        self.assertEqual(graph.outgoing("A"), [ab])
        self.assertEqual(graph.outgoing("C"), [])
        graph.outgoing("A").clear()
        self.assertEqual(graph.outgoing("A"), [ab])
        graph.add_edge("A", "", {"claim": "unresolved"})
        self.assertEqual(graph.outgoing("A"), [ab])

    def test_catalogue_tree_groups_by_ids_and_serializes_recursively(self):
        records = [
            {"brand_id": "b1", "brand_name": "Brand", "product_id": "p1", "product_name": "Base", "shade_id": "s1", "shade_name": "01"},
            {"brand_id": "b1", "brand_name": "Brand", "product_id": "p1", "product_name": "Base", "shade_id": "s2", "shade_name": "01"},
            {"brand_id": "b1", "brand_name": "Brand", "product_id": "p2", "product_name": "Base", "shade_id": "s3", "shade_name": "02"},
        ]
        result = catalogue_node_to_dict(build_catalogue_tree(records))
        self.assertEqual([brand["id"] for brand in result["children"]], ["b1"])
        products = result["children"][0]["children"]
        self.assertEqual([product["id"] for product in products], ["p1", "p2"])
        self.assertEqual([shade["id"] for shade in products[0]["children"]], ["s1", "s2"])
        self.assertEqual(products[0]["children"][0]["shade"]["shade_id"], "s1")


if __name__ == "__main__":
    unittest.main()
