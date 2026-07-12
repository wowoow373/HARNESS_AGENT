"""
topic_code prompts and parsers — shared assets for Generator-Validator QA loop.

This module is extracted from the topic_code research codebase and adapted for
the customer-service agent in harness_agent. It provides:

  1. Three system prompts (Draft / Final / Validator) for the evidence-only
     Generator-Validator approach.
  2. Three user-content builders that construct the input for each LLM call.
  3. Five parsers that extract structured data from LLM text outputs.

Design note: Validator id_map uses {internal_id: display_id} (e.g. {"abc": "N0"})
to match the SubGraphManager.get_id_map() convention in this project.
"""

import re

# ═══════════════════════════════════════════════════════════════════════════════
# System Prompts
# ═══════════════════════════════════════════════════════════════════════════════

CORE_DRAFT_SYSTEM_PROMPT_EVIDENCE_ONLY = (
    "You are a state-transition planner for multi-hop question answering.\n"
    "Your task is to analyze the current reasoning state and propose the next "
    "evidence-seeking facts to investigate.\n\n"
    "Input format:\n"
    "  ORIGINAL QUESTION: <the full question>\n"
    "  CURRENT STATE:\n"
    "    - Evidence passages: the selected passages discovered so far.\n"
    "    - Confirmed fact chain: the sequence of confirmed subject | relation | object facts.\n\n"
    "You do NOT fill in objects. You only propose one-hop evidence-seeking directions.\n\n"
    "You MUST output in this exact format:\n\n"
    "<remaining_question>\n"
    "<rewritten sub-question based only on the original question and confirmed facts>\n"
    "</remaining_question>\n\n"
    "<next_facts>\n"
    "1. subject | relation | ?\n"
    "2. subject | relation | ?\n"
    "...\n"
    "</next_facts>\n\n"
    "CRITICAL RULES:\n"
    "1. The <remaining_question> must be a genuine rewrite. Do NOT copy the original "
    "question verbatim.\n"
    "2. If the confirmed fact chain is empty, each subject should be an entity "
    "mentioned in the original question.\n"
    "3. If the confirmed fact chain is non-empty, prefer subjects that appear as "
    "objects in the confirmed fact chain, typically the most recent object.\n"
    "4. Each candidate must be one-hop only. Do NOT fill in the object. Do NOT skip "
    "directly to the final answer.\n"
    "5. Each relation must be useful for answering the remaining question.\n"
    "6. For bridge questions, propose diverse plausible bridge directions instead of "
    "only copying the surface relation from the original question.\n"
    "7. No duplicate subject-relation pairs already in the confirmed fact chain.\n"
    "8. Do not use outside factual knowledge to fill or assume the object.\n"
    "9. Output ONLY the two XML blocks. No explanations."
)

CORE_FINAL_SYSTEM_PROMPT_EVIDENCE_ONLY = (
    "You are an evidence-only information extractor for multi-hop question answering.\n\n"
    "You are given:\n"
    "  - The ORIGINAL QUESTION.\n"
    "  - The CURRENT STATE: a chain of confirmed facts leading to this step.\n"
    "  - A CANDIDATE DIRECTION: subject | relation | ?\n"
    "  - RETRIEVED PASSAGES: passages retrieved for this candidate direction.\n\n"
    "Your task:\n"
    "  Extract the object that completes the fact:\n"
    "  subject | relation | object\n\n"
    "Decision standard:\n"
    "  Use only the retrieved passages. If a passage explicitly or paraphrastically "
    "supports the candidate subject-relation direction, extract the object as stated "
    "in that passage. Do not replace, reject, or correct the extracted object using "
    "outside knowledge.\n\n"
    "Output format:\n"
    "  subject | relation | object | SELECT: idx\n\n"
    "If the retrieved passages contain no information that supports the given "
    "subject-relation direction, output exactly INVALID.\n\n"
    "RULES:\n"
    "- The subject and relation in your output MUST match the candidate direction exactly.\n"
    "- The object must be supported by one retrieved passage.\n"
    "- Support may be expressed by exact wording, paraphrase, possessive form, "
    "apposition, alias, or equivalent wording.\n"
    "- Do not infer the object from the original question.\n"
    "- Do not infer, replace, or verify the object using your prior knowledge.\n"
    "- Do not output INVALID merely because the extracted claim seems unusual.\n"
    "- Output INVALID only when the passages truly contain no usable support for "
    "the given subject and relation.\n"
    "- Output ONLY one line. No explanations."
)

CORE_VALIDATOR_SYSTEM_PROMPT_EVIDENCE_ONLY = """You are a graph-state validator for multi-hop question answering.

Given a question and a subgraph of candidate facts (triples from a beam search), your job is twofold:
  1. Decide which triples should be KEPT because they form a useful reasoning path toward the answer.
  2. Decide whether the kept graph already contains enough information to ANSWER the question.

You only see triples, not source passages. Therefore, validate graph logic, semantic relevance, and answer sufficiency. Do NOT judge whether a triple is true in the real world.

Judge based on:
  - Graph connectivity: which triples chain together through shared entities?
  - Semantic relevance: does this triple help answer the question?
  - Entity-role consistency: is the entity used in the role required by the question?
  - Completeness: does the graph contain all intermediate facts needed to answer?

Evidence-only validation rule:
  - Treat candidate triples as extracted claims.
  - Do NOT discard a triple because it conflicts with your external knowledge.
  - Do NOT correct a triple using your external knowledge.
  - DISCARD a triple only if it is irrelevant, redundant, malformed, disconnected from any useful reasoning path, or logically inconsistent with other triples in the current graph.

For COMPARISON / BRIDGE_COMPARISON questions: the graph MUST contain facts about ALL entities being compared before you can answer. If only one side is covered, output ANSWER: NONE and KEEP the nodes on the productive side so they can be expanded further.

Output format:

First, analyze the graph:
  <structure>: How are nodes connected? Identify reasoning chains.
  <semantic>: Interpret each triple's meaning relative to the question.
  <comprehensive>: What candidate paths exist? Rank by relevance.
  <rethink>: Any alternative interpretations? False positives?
  Final decision logic: Summarize KEEP/DISCARD decisions.

Then, for EVERY non-root node, one per line:
Node N0: KEEP
Node N1: DISCARD
...

Finally, output the answer decision:
  - If the kept graph directly answers the question: ANSWER: <your answer>
  - If the kept graph is incomplete: ANSWER: NONE

Do not output ANSWER: NONE merely because the answer seems unusual or conflicts with external knowledge.

Before finishing, verify: have you output a decision for EVERY node from N0 to the last node?"""


# ═══════════════════════════════════════════════════════════════════════════════
# User Content Builders
# ═══════════════════════════════════════════════════════════════════════════════

def build_core_draft_v3_user_content(
    question: str,
    evidence_passages: list[str],
    confirmed_triples: list[str],
    K: int,
) -> str:
    """Build user content for draft V3 state-transition generation."""
    if evidence_passages:
        passages_block = "\n".join(
            f"[{i + 1}] {p}" for i, p in enumerate(evidence_passages)
        )
    else:
        passages_block = "(Start) No facts collected yet."

    if confirmed_triples:
        chain_block = "\n".join(
            f"[{i + 1}] {t}" for i, t in enumerate(confirmed_triples)
        )
    else:
        chain_block = "(Start) No facts collected yet."

    return (
        f"ORIGINAL QUESTION:\n{question}\n\n"
        f"CURRENT STATE:\n\n"
        f"Evidence passages (in order of discovery):\n{passages_block}\n\n"
        f"Confirmed fact chain:\n{chain_block}\n\n"
        f"First write the REMAINING QUESTION, then propose up to {K} "
        f"one-hop evidence-seeking facts. Leave every object as ?."
    )


def build_core_final_v3_user_content(
    question: str,
    confirmed_triples: list[str],
    retrieved_passages: list[str],
    draft_subject: str,
    draft_relation: str,
) -> str:
    """Build user content for final V3 information extraction."""
    chain_block = (
        "\n".join(f"[{i + 1}] {t}" for i, t in enumerate(confirmed_triples))
        if confirmed_triples
        else "(Start) No facts collected yet."
    )
    passages_block = "\n".join(
        f"[{i}] {p}" for i, p in enumerate(retrieved_passages)
    )

    return (
        f"ORIGINAL QUESTION:\n{question}\n\n"
        f"CURRENT STATE:\n{chain_block}\n\n"
        f"CANDIDATE DIRECTION TO INVESTIGATE:\n"
        f"{draft_subject} | {draft_relation} | ?\n\n"
        f"RETRIEVED PASSAGES:\n{passages_block}\n\n"
        f"Extract the object that completes the candidate direction above "
        f"using only the retrieved passages. Keep the subject and relation "
        f"exactly as given. Do not replace, reject, or correct the object "
        f"using outside knowledge.\n\n"
        f"Output:\n"
        f"  {draft_subject} | {draft_relation} | <object> | SELECT: idx\n\n"
        f"If the retrieved passages do not support this subject-relation "
        f"direction, output INVALID."
    )


def build_core_validator_content_from_merger(
    question: str,
    merger,
) -> str:
    """Build validator user content from a SubGraphManager instance.

    Returns the user content string containing the question and sorted
    graph node lines. The id_map for parse_validator_decisions can be
    obtained separately from merger.get_id_map().

    Args:
        question: The original question.
        merger: A SubGraphManager instance.

    Returns:
        A string with the question and graph nodes suitable as LLM user content.
    """
    from shared.subgraph_manager import SubGraphManager

    if not isinstance(merger, SubGraphManager):
        raise TypeError("merger must be a SubGraphManager instance")

    # Collect non-root nodes sorted by creation_order ascending.
    raw_nodes = []
    for nid in merger._graph.nodes:
        if nid == "ROOT":
            continue
        triple = merger._graph.nodes[nid].get("triple_str", "")
        order = merger._graph.nodes[nid].get("creation_order", 0)
        raw_nodes.append((nid, order, triple))

    raw_nodes.sort(key=lambda x: x[1])

    # Build display lines.
    lines = []
    for idx, (_nid, _order, triple) in enumerate(raw_nodes):
        display_id = f"N{idx}"
        lines.append(f"  [{display_id}] {triple}")

    return (
        f"Question: {question}\n\n"
        f"Graph ({len(raw_nodes)} nodes):\n"
        + "\n".join(lines)
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Parsers
# ═══════════════════════════════════════════════════════════════════════════════

# Regex for parsing draft list output: matches "1. subj | rel | ?" style lines
_DRAFT_LIST_RE = re.compile(
    r"^\s*(\d+)\s*[.)]\s*(.+?)\s*\|\s*(.+?)\s*\|\s*\?\s*$",
    re.MULTILINE,
)

# Regex to match Node Nx: KEEP/DISCARD (case-insensitive)
_DECISION_PATTERN = re.compile(
    r"^Node\s+(N\d+)\s*:\s*(KEEP|DISCARD)",
    re.IGNORECASE | re.MULTILINE,
)

# Regex to match ANSWER: <answer> or ANSWER: NONE (case-insensitive)
_ANSWER_PATTERN = re.compile(
    r"^ANSWER:\s*(.+)$",
    re.IGNORECASE | re.MULTILINE,
)


def parse_draft_list(text: str) -> list[tuple[str, str]]:
    """Parse a draft list output into (subject, relation) tuples.

    Handles numbered list formats:
        1. subj | rel | ?
        2) subj | rel | ?
        3 . subj | rel | ?

    Deduplicates by (subj, rel) while preserving order.
    Returns empty list if nothing parses.
    """
    candidates: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for match in _DRAFT_LIST_RE.finditer(text):
        subj = match.group(2).strip()
        rel = match.group(3).strip()
        key = (subj.lower(), rel.lower())
        if key not in seen:
            seen.add(key)
            candidates.append((subj, rel))
    return candidates


def parse_draft_v3_output(text: str) -> tuple[str | None, list[tuple[str, str]]]:
    """Parse draft V3 output into (remaining_question, candidates)."""
    rq_match = re.search(
        r"<remaining_question>\s*(.*?)\s*</remaining_question>",
        text,
        re.DOTALL | re.IGNORECASE,
    )
    remaining_question = rq_match.group(1).strip() if rq_match else None

    nf_match = re.search(
        r"<next_facts>\s*(.*?)\s*</next_facts>",
        text,
        re.DOTALL | re.IGNORECASE,
    )
    triples_text = nf_match.group(1) if nf_match else text
    candidates = parse_draft_list(triples_text)
    return remaining_question, candidates


def parse_final(text: str) -> tuple[str, str, str, int] | str | None:
    """Parse final output into a confirmed triple or status.

    Returns:
      - (subj, rel, obj, idx) for a valid triple
      - "INVALID" for unsupported direction
      - None for malformed output
    """
    text = text.strip()
    if text.upper() == "INVALID":
        return "INVALID"
    m = re.match(
        r"^(.+?)\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|\s*SELECT:\s*(\d+)\s*$",
        text,
    )
    if not m:
        return None
    return (
        m.group(1).strip(),
        m.group(2).strip(),
        m.group(3).strip(),
        int(m.group(4)),
    )


def parse_validator_decisions(
    text: str,
    id_map: dict[str, str],
) -> dict[str, int]:
    """Parse KEEP/DISCARD decisions from validator output.

    Args:
        text: Raw text output from the validator model.
        id_map: {internal_id: display_id} mapping (e.g. {"abc": "N0"}),
                as returned by SubGraphManager.get_id_map().

    Returns:
        {internal_id: score} where KEEP=1, DISCARD=0.
        Missing nodes default to 0.
    """
    # Initialize all nodes to 0 (DISCARD)
    result: dict[str, int] = {internal_id: 0 for internal_id in id_map}

    # Build reverse map: {display_id: internal_id}
    reverse_map: dict[str, str] = {v: k for k, v in id_map.items()}

    for match in _DECISION_PATTERN.finditer(text):
        display_id = match.group(1)
        decision = match.group(2).upper()
        if display_id in reverse_map:
            result[reverse_map[display_id]] = 1 if decision == "KEEP" else 0

    return result


def parse_validator_answer(text: str) -> str | None:
    """Parse the ANSWER line from validator output.

    Returns:
        The answer string if ANSWER: <something not NONE> is found.
        None if ANSWER: NONE or no ANSWER line found.
    """
    text = text.strip()
    if not text:
        return None
    # Find last ANSWER line (in case model outputs multiple)
    matches = list(_ANSWER_PATTERN.finditer(text))
    if not matches:
        return None
    answer = matches[-1].group(1).strip()
    if answer.upper() == "NONE":
        return None
    return answer
