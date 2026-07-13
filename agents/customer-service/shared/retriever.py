"""Retriever — document retrieval for evidence anchoring."""


class RetrieverStub:
    """Abstract retriever interface matching topic_code's contract."""
    def retrieve(self, query: str, corpus: list[str], top_k: int) -> list[str]:
        ...


class InMemoryRetriever(RetrieverStub):
    """Keyword-overlap retriever. Replaceable with BM25 or Dense.

    Args:
        corpus: [(title, [sentence_1, sentence_2, ...])]
    """

    def __init__(self, corpus: list[tuple[str, list[str]]]):
        self._flattened = []
        for title, sentences in corpus:
            for s in sentences:
                self._flattened.append(f"[{title}] {s}")

    def retrieve(self, query: str, corpus: list[str], top_k: int) -> list[str]:
        query_chars = set(query)
        scored = []
        for doc in self._flattened:
            doc_chars = set(doc)
            score = len(query_chars & doc_chars)
            if score > 0:
                scored.append((score, doc))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [doc for _, doc in scored[:top_k]]
