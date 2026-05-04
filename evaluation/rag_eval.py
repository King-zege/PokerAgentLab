"""Evaluate StrategyRAG retrieval against a small human-labeled dataset."""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from memory.strategy_rag import StrategyRAG


DEFAULT_DATASET_PATH = "evaluation/datasets/strategy_queries.jsonl"
DEFAULT_REPORT_DIR = "data/evaluation"
REQUIRED_FIELDS = {"query", "relevant_chunk_ids"}


@dataclass
class RagEvalCase:
    id: str
    query: str
    relevant_chunk_ids: list[str]
    street: str | None = None
    style: str | None = None
    hand_class: str | None = None
    position: str | None = None
    action_tags: list[str] | None = None
    spr_tags: list[str] | None = None


def run_rag_evaluation(
    dataset_path: str = DEFAULT_DATASET_PATH,
    top_k: int = 3,
    run_id: str | None = None,
    report_dir: str = DEFAULT_REPORT_DIR,
) -> dict[str, Any]:
    """Run retrieval evaluation and persist JSON/Markdown reports."""
    run_id = run_id or f"rag_{uuid.uuid4().hex[:8]}"
    cases = load_rag_dataset(dataset_path)
    rag = StrategyRAG()
    case_results = [_evaluate_case(rag, case, top_k) for case in cases]
    metrics = _aggregate_metrics(case_results, top_k)

    report = {
        "run_id": run_id,
        "kind": "rag",
        "dataset_path": dataset_path,
        "top_k": top_k,
        "case_count": len(cases),
        "metrics": metrics,
        "cases": case_results,
    }
    paths = _write_report(report, report_dir, prefix="rag_eval")
    report.update(paths)
    return report


def load_rag_evaluation(run_id: str, report_dir: str = DEFAULT_REPORT_DIR) -> dict[str, Any] | None:
    path = Path(report_dir) / f"rag_eval_{run_id}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def load_rag_dataset(dataset_path: str) -> list[RagEvalCase]:
    path = Path(dataset_path)
    if not path.exists():
        raise ValueError(f"RAG eval dataset not found: {dataset_path}")

    cases: list[RagEvalCase] = []
    with path.open("r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSON at {dataset_path}:{line_no}") from exc
            missing = sorted(REQUIRED_FIELDS - set(raw))
            if missing:
                raise ValueError(f"Missing required fields at {dataset_path}:{line_no}: {', '.join(missing)}")
            relevant = raw.get("relevant_chunk_ids")
            if not isinstance(relevant, list) or not relevant:
                raise ValueError(f"relevant_chunk_ids must be a non-empty list at {dataset_path}:{line_no}")
            cases.append(
                RagEvalCase(
                    id=str(raw.get("id") or f"case_{line_no}"),
                    query=str(raw["query"]),
                    relevant_chunk_ids=[str(item) for item in relevant],
                    street=raw.get("street"),
                    style=raw.get("style"),
                    hand_class=raw.get("hand_class"),
                    position=raw.get("position"),
                    action_tags=raw.get("action_tags") or [],
                    spr_tags=raw.get("spr_tags") or [],
                )
            )
    if not cases:
        raise ValueError(f"RAG eval dataset is empty: {dataset_path}")
    return cases


def compute_retrieval_metrics(retrieved_ids: list[str], relevant_ids: list[str], top_k: int) -> dict[str, Any]:
    """Compute ranking metrics for one retrieval result."""
    top_k = max(1, top_k)
    relevant = set(relevant_ids)
    top = retrieved_ids[:top_k]
    hits = [chunk_id for chunk_id in top if chunk_id in relevant]
    first_rank = next((idx + 1 for idx, chunk_id in enumerate(retrieved_ids) if chunk_id in relevant), None)
    return {
        "hit_at_1": 1.0 if retrieved_ids[:1] and retrieved_ids[0] in relevant else 0.0,
        "hit_at_3": 1.0 if any(chunk_id in relevant for chunk_id in retrieved_ids[:3]) else 0.0,
        "precision_at_k": round(len(hits) / top_k, 4),
        "recall_at_k": round(len(hits) / len(relevant), 4) if relevant else 0.0,
        "mrr": round(1 / first_rank, 4) if first_rank else 0.0,
    }


def _evaluate_case(rag: StrategyRAG, case: RagEvalCase, top_k: int) -> dict[str, Any]:
    started = time.perf_counter()
    chunks = rag.search(
        query=case.query,
        street=case.street,
        style=case.style,
        limit=top_k,
        hand_class=case.hand_class,
        position=case.position,
        action_tags=case.action_tags,
        spr_tags=case.spr_tags,
    )
    latency_ms = round((time.perf_counter() - started) * 1000, 3)
    retrieved_ids = [chunk["id"] for chunk in chunks]
    metrics = compute_retrieval_metrics(retrieved_ids, case.relevant_chunk_ids, top_k)
    missed = [chunk_id for chunk_id in case.relevant_chunk_ids if chunk_id not in retrieved_ids[:top_k]]
    return {
        "id": case.id,
        "query": case.query,
        "expected_chunk_ids": case.relevant_chunk_ids,
        "retrieved_chunk_ids": retrieved_ids,
        "metrics": metrics,
        "latency_ms": latency_ms,
        "miss_reason": "all relevant chunks retrieved" if not missed else f"missing relevant chunks in top {top_k}: {', '.join(missed)}",
        "retrieved_chunks": [
            {
                "id": chunk["id"],
                "title": chunk["title"],
                "score": chunk["score"],
                "reason": chunk["reason"],
                "score_breakdown": chunk["score_breakdown"],
                "source": chunk["source"],
            }
            for chunk in chunks
        ],
    }


def _aggregate_metrics(case_results: list[dict[str, Any]], top_k: int) -> dict[str, Any]:
    if not case_results:
        return {
            "hit_at_1": 0.0,
            "hit_at_3": 0.0,
            "precision_at_k": 0.0,
            "recall_at_k": 0.0,
            "mrr": 0.0,
            "avg_latency_ms": 0.0,
        }
    count = len(case_results)
    keys = ("hit_at_1", "hit_at_3", "precision_at_k", "recall_at_k", "mrr")
    metrics = {key: round(sum(case["metrics"][key] for case in case_results) / count, 4) for key in keys}
    metrics["avg_latency_ms"] = round(sum(case["latency_ms"] for case in case_results) / count, 3)
    metrics["top_k"] = top_k
    return metrics


def _write_report(report: dict[str, Any], report_dir: str, prefix: str) -> dict[str, str]:
    path = Path(report_dir)
    path.mkdir(parents=True, exist_ok=True)
    json_path = path / f"{prefix}_{report['run_id']}.json"
    markdown_path = path / f"{prefix}_{report['run_id']}.md"
    report["report_path"] = str(json_path)
    report["markdown_path"] = str(markdown_path)
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    markdown_path.write_text(_to_markdown(report), encoding="utf-8")
    return {"report_path": str(json_path), "markdown_path": str(markdown_path)}


def _to_markdown(report: dict[str, Any]) -> str:
    metrics = report["metrics"]
    lines = [
        f"# StrategyRAG Evaluation: {report['run_id']}",
        "",
        f"- Dataset: `{report['dataset_path']}`",
        f"- Cases: {report['case_count']}",
        f"- Top K: {report['top_k']}",
        f"- Hit@1: {metrics['hit_at_1']:.2%}",
        f"- Hit@3: {metrics['hit_at_3']:.2%}",
        f"- Precision@K: {metrics['precision_at_k']:.2%}",
        f"- Recall@K: {metrics['recall_at_k']:.2%}",
        f"- MRR: {metrics['mrr']:.4f}",
        f"- Avg latency: {metrics['avg_latency_ms']} ms",
        "",
        "| Case | Expected | Retrieved | Recall@K | Reason |",
        "|---|---|---|---:|---|",
    ]
    for case in report["cases"]:
        expected = ", ".join(case["expected_chunk_ids"])
        retrieved = ", ".join(case["retrieved_chunk_ids"])
        reason = case["miss_reason"]
        lines.append(f"| {case['id']} | {expected} | {retrieved} | {case['metrics']['recall_at_k']:.2%} | {reason} |")
    lines.append("")
    return "\n".join(lines)
