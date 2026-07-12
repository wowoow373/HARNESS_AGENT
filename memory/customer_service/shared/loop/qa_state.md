---
key: qa_state
namespace: loop
timestamp: 1783880142.4945397
---
{"question": "改签规则是什么？", "round": 1, "max_hops": 4, "phase": "done", "expandable": ["ROOT"], "graph": {"directed": true, "multigraph": false, "graph": {}, "nodes": [{"triple_str": "ROOT", "creation_order": -1, "id": "ROOT"}, {"triple_str": "改签规则 | 适用于 | 旅客", "accumulated_passages": null, "select_idx": 1, "retrieved_passages": [], "creation_order": 1, "score": 1, "id": "3d9c0e21756d"}], "edges": [{"source": "ROOT", "target": "3d9c0e21756d"}]}, "pending": {"total": 2, "received": 2, "results": [{"valid": true, "triple": ["改签规则", "适用于", "旅客"], "node_id": "3d9c0e21756d", "select_idx": 1}, {"valid": false, "reason": "INVALID"}]}, "K": 2, "top_k_retrieve": 5, "tried_candidates": {"ROOT": [["改签规则", "适用于"], ["改签规则", "定义"]]}, "answer": "QA模块暂未检索到相关信息，无法回答您的问题，建议转接人工客服。", "sources": null}