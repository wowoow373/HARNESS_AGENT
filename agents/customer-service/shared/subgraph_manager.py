"""SubGraphManager — multi-hop reasoning state graph on networkx."""
import networkx as nx
from uuid import uuid4


class SubGraphManager:
    """Multi-hop reasoning state graph.

    Node attributes:
    - triple_str: "subj | rel | obj", ROOT node is "ROOT"
    - accumulated_passages: str
    - select_idx: int | None
    - retrieved_passages: list[str]
    - creation_order: int
    - score: int (0=DISCARD, 1=KEEP, -1=unscored)
    """

    def __init__(self):
        self._graph = nx.DiGraph()
        self._counter = 0
        self._graph.add_node("ROOT", triple_str="ROOT", creation_order=-1)

    def add_node(
        self,
        triple_str: str,
        parent_id: str | None = None,
        accumulated_passages: str | None = None,
        select_idx: int | None = None,
        retrieved_passages: list[str] | None = None,
    ) -> str:
        node_id = uuid4().hex[:12]
        self._counter += 1
        self._graph.add_node(
            node_id,
            triple_str=triple_str,
            accumulated_passages=accumulated_passages,
            select_idx=select_idx,
            retrieved_passages=retrieved_passages or [],
            creation_order=self._counter,
            score=-1,
        )
        if parent_id:
            self._graph.add_edge(parent_id, node_id)
        return node_id

    def get_path_triples(self, node_id: str) -> list[str]:
        """Triple chain from ROOT (exclusive) to node_id (inclusive)."""
        if node_id == "ROOT":
            return []
        triples = []
        current = node_id
        while current != "ROOT":
            triples.append(self._graph.nodes[current]["triple_str"])
            preds = list(self._graph.predecessors(current))
            current = preds[0] if preds else "ROOT"
        return list(reversed(triples))

    def get_leaf_nodes(self) -> list[str]:
        return [n for n in self._graph.nodes
                if n != "ROOT" and self._graph.out_degree(n) == 0]

    def get_accumulated_passages(self, node_id: str) -> str:
        passages = []
        current = node_id
        while current is not None and current != "ROOT":
            ap = self._graph.nodes[current].get("accumulated_passages", "")
            if ap:
                passages.append(ap)
            preds = list(self._graph.predecessors(current))
            current = preds[0] if preds else None
        return "\n".join(reversed(passages))

    def update_scores(self, decisions: dict[str, int]):
        for nid, score in decisions.items():
            if nid in self._graph.nodes:
                self._graph.nodes[nid]["score"] = score

    def get_id_map(self) -> dict[str, str]:
        non_root = [n for n in self._graph.nodes if n != "ROOT"]
        non_root.sort(key=lambda n: self._graph.nodes[n].get("creation_order", 0))
        return {nid: f"N{i}" for i, nid in enumerate(non_root)}

    def get_sources(self) -> list[str]:
        sources = []
        for nid in self._graph.nodes:
            if nid != "ROOT" and self._graph.nodes[nid].get("score") == 1:
                triple = self._graph.nodes[nid]["triple_str"]
                passages = self._graph.nodes[nid].get("retrieved_passages", [])
                idx = self._graph.nodes[nid].get("select_idx")
                if idx is not None and idx < len(passages):
                    sources.append(f"[{triple}] {passages[idx]}")
        return sources

    def node_count(self) -> int:
        return len([n for n in self._graph.nodes if n != "ROOT"])

    def to_dict(self) -> dict:
        return nx.node_link_data(self._graph, edges="edges")

    @classmethod
    def from_dict(cls, data: dict) -> "SubGraphManager":
        instance = cls()
        instance._graph = nx.node_link_graph(data, edges="edges")
        if "ROOT" not in instance._graph.nodes:
            instance._graph.add_node("ROOT", triple_str="ROOT", creation_order=-1)
        instance._counter = instance.node_count()
        return instance
