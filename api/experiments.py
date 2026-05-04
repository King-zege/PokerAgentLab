"""Self-play experiment runner and report generation."""

from __future__ import annotations

import json
import sys
import uuid
from collections import defaultdict
from pathlib import Path
from typing import Any

import yaml

from engine.game import Game
from memory.memory_manager_agent import MemoryManagerAgent


DEFAULT_SELF_PLAYERS = [
    {"id": "TAG", "agent_type": "style_fallback", "style": "tag", "stack_bb": 100},
    {"id": "LAG", "agent_type": "style_fallback", "style": "lag", "stack_bb": 100},
    {"id": "Balanced", "agent_type": "style_fallback", "style": "balanced", "stack_bb": 100},
    {"id": "Nit", "agent_type": "style_fallback", "style": "nit", "stack_bb": 100},
]


def run_self_play_experiment(
    config_path: str,
    num_hands: int,
    seed: int | None = 42,
    players: list[dict] | None = None,
    experiment_id: str | None = None,
) -> dict[str, Any]:
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    experiment_id = experiment_id or f"exp_{uuid.uuid4().hex[:8]}"
    reports_dir = Path("data/reports")
    reports_dir.mkdir(parents=True, exist_ok=True)

    base_config = _load_config(config_path)
    experiment_config = dict(base_config)
    experiment_config["players"] = players or DEFAULT_SELF_PLAYERS
    experiment_config.setdefault("table", {})["size"] = len(experiment_config["players"])
    experiment_config.setdefault("session", {})["num_hands"] = num_hands
    experiment_config["session"]["seed"] = seed
    experiment_config["session"]["history_file"] = "hand_history.jsonl"
    experiment_config["session"]["decision_log"] = "decision_log.txt"
    experiment_config.setdefault("llm", {})["enabled"] = False
    experiment_config["llm"]["api_key"] = ""

    config_out = Path("config") / f"generated_{experiment_id}.yaml"
    with open(config_out, "w", encoding="utf-8") as f:
        yaml.safe_dump(experiment_config, f, allow_unicode=True, sort_keys=False)

    game = Game(str(config_out), session_id=experiment_id)
    game.play_session(num_hands=num_hands, interactive=False)
    histories = game.history_store.load_all()
    summary = _summarize(histories, experiment_config["players"])
    memory_agent_report = MemoryManagerAgent().run_session(
        experiment_id,
        histories=histories,
        focus_player_id=None,
        force=True,
    )

    report_path = reports_dir / f"self_play_{experiment_id}.json"
    markdown_path = reports_dir / f"self_play_{experiment_id}.md"
    report = {
        "experiment_id": experiment_id,
        "num_hands": len(histories),
        "seed": seed,
        "players": experiment_config["players"],
        "summary": summary,
        "memory_agent_report_path": memory_agent_report.get("report_path"),
        "memory_agent_summary": memory_agent_report.get("governance_summary", {}),
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    markdown_path.write_text(_to_markdown(report), encoding="utf-8")

    return {
        "experiment_id": experiment_id,
        "num_hands": len(histories),
        "seed": seed,
        "report_path": str(report_path),
        "markdown_path": str(markdown_path),
        "summary": summary,
        "memory_agent_report_path": memory_agent_report.get("report_path"),
        "memory_agent_summary": memory_agent_report.get("governance_summary", {}),
    }


def load_experiment_report(experiment_id: str) -> dict[str, Any] | None:
    path = Path("data/reports") / f"self_play_{experiment_id}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _load_config(config_path: str) -> dict:
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _summarize(histories: list, players: list[dict]) -> dict[str, Any]:
    stats: dict[str, dict[str, Any]] = {}
    initial_stacks = {p["id"]: float(p.get("stack_bb", 0)) for p in players}

    for p in players:
        stats[p["id"]] = {
            "hands": 0,
            "wins": 0,
            "profit_bb": 0.0,
            "vpip": 0,
            "pfr": 0,
            "bets_or_raises": 0,
            "calls": 0,
            "actions": defaultdict(int),
        }

    for h in histories:
        for pid in stats:
            stats[pid]["hands"] += 1
            player_actions = [a for a in h.actions if a.player_id == pid]
            action_text = " ".join(a.action.lower() for a in player_actions)
            if any(x in action_text for x in ("call", "raise", "bet", "all_in")):
                stats[pid]["vpip"] += 1
            if any(a.street == "preflop" and any(x in a.action.lower() for x in ("raise", "bet", "all_in")) for a in player_actions):
                stats[pid]["pfr"] += 1
            for a in player_actions:
                lower = a.action.lower()
                action_type = lower.split()[0] if lower else "unknown"
                stats[pid]["actions"][action_type] += 1
                if any(x in lower for x in ("raise", "bet", "all_in")):
                    stats[pid]["bets_or_raises"] += 1
                if "call" in lower:
                    stats[pid]["calls"] += 1

        for pot in h.pots:
            for winner in pot.get("winners", []):
                pid = winner.get("player")
                if pid in stats:
                    stats[pid]["wins"] += 1

    if histories:
        for pid, final_stack in histories[-1].final_stacks.items():
            if pid in stats:
                stats[pid]["profit_bb"] = final_stack - initial_stacks.get(pid, 0.0)

    result: dict[str, Any] = {}
    for pid, s in stats.items():
        hands = max(1, s["hands"])
        calls = s["calls"]
        result[pid] = {
            "hands": s["hands"],
            "wins": s["wins"],
            "win_rate": round(s["wins"] / hands, 4),
            "profit_bb": round(s["profit_bb"], 2),
            "bb_per_100": round(s["profit_bb"] / hands * 100, 2),
            "vpip": round(s["vpip"] / hands, 4),
            "pfr": round(s["pfr"] / hands, 4),
            "aggression_factor": round(s["bets_or_raises"] / calls, 2) if calls else float(s["bets_or_raises"]),
            "action_distribution": dict(s["actions"]),
        }
    return result


def _to_markdown(report: dict[str, Any]) -> str:
    lines = [
        f"# Self-Play Report: {report['experiment_id']}",
        "",
        f"- Hands: {report['num_hands']}",
        f"- Seed: {report['seed']}",
        f"- Memory Agent Report: {report.get('memory_agent_report_path') or 'not generated'}",
        "",
        "| Player | Win Rate | Profit BB | BB/100 | VPIP | PFR | AF |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for pid, s in report["summary"].items():
        lines.append(
            f"| {pid} | {s['win_rate']:.2%} | {s['profit_bb']} | {s['bb_per_100']} | "
            f"{s['vpip']:.2%} | {s['pfr']:.2%} | {s['aggression_factor']} |"
        )
    lines.append("")
    return "\n".join(lines)
