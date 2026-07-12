"""Unit tests for SubGraphManager."""
import pytest
from shared.subgraph_manager import SubGraphManager


class TestSubGraphManager:

    def test_init_has_root_node(self):
        g = SubGraphManager()
        d = g.to_dict()
        nodes = {n["id"] for n in d["nodes"]}
        assert "ROOT" in nodes

    def test_add_node_returns_id(self):
        g = SubGraphManager()
        nid = g.add_node("A | B | C", parent_id="ROOT")
        assert nid is not None
        assert len(nid) == 12  # uuid4 hex[:12]

    def test_add_node_creates_edge_to_parent(self):
        g = SubGraphManager()
        nid = g.add_node("A | B | C", parent_id="ROOT")
        d = g.to_dict()
        edges = [(e["source"], e["target"]) for e in d["edges"]]
        assert ("ROOT", nid) in edges

    def test_get_path_triples_root_node(self):
        g = SubGraphManager()
        assert g.get_path_triples("ROOT") == []

    def test_get_path_triples_leaf(self):
        g = SubGraphManager()
        n1 = g.add_node("A | B | C", parent_id="ROOT")
        n2 = g.add_node("C | D | E", parent_id=n1)
        triples = g.get_path_triples(n2)
        assert triples == ["A | B | C", "C | D | E"]

    def test_get_leaf_nodes(self):
        g = SubGraphManager()
        n1 = g.add_node("A | B | C", parent_id="ROOT")
        n2 = g.add_node("C | D | E", parent_id=n1)
        leaves = g.get_leaf_nodes()
        assert n2 in leaves
        assert n1 not in leaves

    def test_get_id_map(self):
        g = SubGraphManager()
        n1 = g.add_node("First", parent_id="ROOT")
        n2 = g.add_node("Second", parent_id="ROOT")
        id_map = g.get_id_map()
        assert id_map[n1] == "N0"
        assert id_map[n2] == "N1"

    def test_update_scores(self):
        g = SubGraphManager()
        n1 = g.add_node("A | B | C", parent_id="ROOT")
        g.update_scores({n1: 1})
        d = g.to_dict()
        node = next(n for n in d["nodes"] if n["id"] == n1)
        assert node["score"] == 1

    def test_roundtrip_serialization(self):
        g = SubGraphManager()
        n1 = g.add_node("A | B | C", parent_id="ROOT",
                        accumulated_passages="passage 1",
                        select_idx=0,
                        retrieved_passages=["passage 1"])
        g.update_scores({n1: 1})
        data = g.to_dict()
        g2 = SubGraphManager.from_dict(data)
        assert g2.get_path_triples(n1) == ["A | B | C"]
        d2 = g2.to_dict()
        node = next(n for n in d2["nodes"] if n["id"] == n1)
        assert node["score"] == 1
        assert node["select_idx"] == 0

    def test_from_dict_ensures_root(self):
        data = {"nodes": [], "edges": [], "directed": True, "multigraph": False, "graph": {}}
        g = SubGraphManager.from_dict(data)
        d = g.to_dict()
        nodes = {n["id"] for n in d["nodes"]}
        assert "ROOT" in nodes

    def test_get_sources(self):
        g = SubGraphManager()
        n1 = g.add_node("A | B | C", parent_id="ROOT",
                        retrieved_passages=["source text"],
                        select_idx=0)
        g.update_scores({n1: 1})
        sources = g.get_sources()
        assert len(sources) == 1
        assert "source text" in sources[0]
