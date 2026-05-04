"""Evaluate PokerAgentLab usefulness signals through repeatable self-play runs."""

from __future__ import annotations

import json
import os
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from analysis.analysis_agent import AnalysisAgent
from analysis.coach_agent import CoachAgent
from api.experiments import run_self_play_experiment
from memory.decision_trace import DecisionTraceStore
from memory.history_store import HistoryStore
from strategy.style_profile import StyleRegistry


DEFAULT_REPORT_DIR = "data/evaluation"
DEFAULT_VARIANTS = ["baseline", "rag", "memory"]
VARIANT_ENV = {
    "baseline": {"POKER_MEMORY_ENABLED": "false", "POKER_STRATEGY_RAG_ENABLED": "false"},
    "rag": {"POKER_MEMORY_ENABLED": "false", "POKER_STRATEGY_RAG_ENABLED": "true"},
    "memory": {"POKER_MEMORY_ENABLED": "true", "POKER_STRATEGY_RAG_ENABLED": "true"},
}


def run_system_evaluation(
    config_path: str = "config/game_config.yaml",
    num_hands: int = 20,
    seed: int | None = 42,
    variants: list[str] | None = None,
    run_id: str | None = None,
    report_dir: str = DEFAULT_REPORT_DIR,
) -> dict[str, Any]:
    """Run repeatable self-play variants and persist evaluation reports."""
    run_id = run_id or f"system_{uuid.uuid4().hex[:8]}"
    selected = variants or DEFAULT_VARIANTS
    variant_reports = []
    for variant in selected:
        env = VARIANT_ENV.get(variant, VARIANT_ENV["baseline"])
        experiment_id = f"{run_id}_{variant}"
        with _temporary_env(env):
            self_play = run_self_play_experiment(
                config_path=config_path,
                num_hands=num_hands,
                seed=seed,
                experiment_id=experiment_id,
            )
        variant_reports.append(_summarize_variant(variant, experiment_id, self_play))

    report = {
        "run_id": run_id,
        "kind": "system",
        "num_hands": num_hands,
        "seed": seed,
        "variants": variant_reports,
        "summary": _compare_variants(variant_reports),
    }
    paths = _write_report(report, report_dir, prefix="system_eval")
    report.update(paths)
    return report


def load_system_evaluation(run_id: str, report_dir: str = DEFAULT_REPORT_DIR) -> dict[str, Any] | None:
    path = Path(report_dir) / f"system_eval_{run_id}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _summarize_variant(variant: str, experiment_id: str, self_play: dict[str, Any]) -> dict[str, Any]:
    histories = HistoryStore(f"data/history/hand_history_{experiment_id}.jsonl").load_all()
    traces = DecisionTraceStore.for_session(experiment_id).load_all()
    total_actions = sum(len(hand.actions) for hand in histories)
    traced_actions = len(traces)
    fallback_count = sum(1 for trace in traces if trace.get("fallback_reason"))
    strategy_traced = sum(1 for trace in traces if trace.get("retrieved_strategy_chunk_ids"))
    memory_traced = sum(1 for trace in traces if trace.get("retrieved_memory_ids"))

    coach = CoachAgent(AnalysisAgent(StyleRegistry("config/styles")))
    coach_report = coach.review_session(histories)
    training_plan = coach_report.get("training_plan") or coach_report.get("training_goals") or []

    return {
        "variant": variant,
        "experiment_id": experiment_id,
        "self_play": self_play,
        "trace_metrics": {
            "total_actions": total_actions,
            "traced_actions": traced_actions,
            "trace_coverage": round(traced_actions / total_actions, 4) if total_actions else 0.0,
            "fallback_count": fallback_count,
            "strategy_trace_count": strategy_traced,
            "strategy_trace_coverage": round(strategy_traced / traced_actions, 4) if traced_actions else 0.0,
            "memory_trace_count": memory_traced,
            "memory_trace_coverage": round(memory_traced / traced_actions, 4) if traced_actions else 0.0,
        },
        "coach_metrics": {
            "has_training_plan": bool(training_plan),
            "training_plan_count": len(training_plan),
            "critical_spot_count": len(coach_report.get("critical_spots", [])),
            "leak_candidate_count": len(coach_report.get("leak_candidates", [])),
        },
    }


def _compare_variants(variants: list[dict[str, Any]]) -> dict[str, Any]:
    best_by_bb100 = None
    rows = []
    for variant in variants:
        player_summary = variant["self_play"].get("summary", {})
        avg_bb100 = _average([player.get("bb_per_100", 0.0) for player in player_summary.values()])
        row = {
            "variant": variant["variant"],
            "avg_bb_per_100": avg_bb100,
            "trace_coverage": variant["trace_metrics"]["trace_coverage"],
            "strategy_trace_coverage": variant["trace_metrics"]["strategy_trace_coverage"],
            "memory_trace_coverage": variant["trace_metrics"]["memory_trace_coverage"],
            "fallback_count": variant["trace_metrics"]["fallback_count"],
            "has_training_plan": variant["coach_metrics"]["has_training_plan"],
        }
        rows.append(row)
        if best_by_bb100 is None or row["avg_bb_per_100"] > best_by_bb100["avg_bb_per_100"]:
            best_by_bb100 = row

    return {
        "note": "Style-agent self-play is deterministic evaluation scaffolding; RAG/Memory coverage indicates context availability, not proven human learning lift.",
        "best_by_avg_bb_per_100": best_by_bb100,
        "rows": rows,
    }


def _average(values: list[float]) -> float:
    return round(sum(values) / len(values), 4) if values else 0.0


@contextmanager
def _temporary_env(values: dict[str, str]) -> Iterator[None]:
    old = {key: os.environ.get(key) for key in values}
    try:
        os.environ.update(values)
        yield
    finally:
        for key, value in old.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


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
    lines = [
        f"# System Evaluation: {report['run_id']}",
        "",
        f"- Hands per variant: {report['num_hands']}",
        f"- Seed: {report['seed']}",
        f"- Note: {report['summary']['note']}",
        "",
        "| Variant | Avg BB/100 | Trace Coverage | Strategy Coverage | Memory Coverage | Fallbacks | Training Plan |",
        "|---|---:|---:|---:|---:|---:|---|",
    ]
    for row in report["summary"]["rows"]:
        lines.append(
            f"| {row['variant']} | {row['avg_bb_per_100']} | {row['trace_coverage']:.2%} | "
            f"{row['strategy_trace_coverage']:.2%} | {row['memory_trace_coverage']:.2%} | "
            f"{row['fallback_count']} | {'yes' if row['has_training_plan'] else 'no'} |"
        )
    lines.append("")
    return "\n".join(lines)
