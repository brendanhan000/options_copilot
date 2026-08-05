"""Aggregate grades into a pattern report: process vs. luck, biases, P&L cuts."""

from collections import Counter, defaultdict
from typing import Dict

_GPA = {"A": 4.0, "B": 3.0, "C": 2.0, "D": 1.0, "F": 0.0}

# The schema says "luck_win"; tolerate "lucky_win" from manual replies.
_ATTR_ALIASES = {"lucky_win": "luck_win", "skill_win": "skill_win"}


def _norm_attr(value) -> str:
    v = str(value or "unknown").strip().lower()
    return _ATTR_ALIASES.get(v, v)


def summarize(journal) -> Dict:
    grades = journal.all_grades()
    n = len(grades)
    total_pnl = sum(g.get("realized_pnl") or 0.0 for g in grades)
    wins = sum(1 for g in grades if (g.get("realized_pnl") or 0.0) > 0)

    gpa_points = []
    grade_dist = Counter()
    attribution = Counter()
    no_invalidation = 0
    bias_counts = Counter()
    bias_pnl = defaultdict(float)
    pnl_by_regime_fit = defaultdict(float)
    pnl_by_strategy = defaultdict(float)

    for g in grades:
        grade = g["grade"]
        pnl = g.get("realized_pnl") or 0.0
        letter = str(grade.get("thesis_grade") or "?").strip().upper()[:1]
        grade_dist[letter] += 1
        if letter in _GPA:
            gpa_points.append(_GPA[letter])
        attribution[_norm_attr(grade.get("attribution"))] += 1
        if grade.get("invalidation_defined") is False:
            no_invalidation += 1
        for tag in grade.get("bias_tags") or []:
            name = tag.get("tag") if isinstance(tag, dict) else str(tag)
            if name:
                bias_counts[name] += 1
                bias_pnl[name] += pnl
        pnl_by_regime_fit[str(grade.get("regime_fit") or "unknown")] += pnl
        pnl_by_strategy[str(g.get("strategy") or "unknown")] += pnl

    return {
        "n": n,
        "total_pnl": round(total_pnl, 2),
        "win_rate": round(wins / n, 3) if n else None,
        "avg_gpa": round(sum(gpa_points) / len(gpa_points), 2) if gpa_points else None,
        "attribution": dict(attribution),
        "no_invalidation_count": no_invalidation,
        "top_biases": [
            {"tag": t, "count": c, "pnl": round(bias_pnl[t], 2)}
            for t, c in bias_counts.most_common(10)
        ],
        "pnl_by_regime_fit": {k: round(v, 2) for k, v in pnl_by_regime_fit.items()},
        "pnl_by_strategy": {k: round(v, 2) for k, v in pnl_by_strategy.items()},
        "grade_distribution": dict(grade_dist),
    }


def format_report(summary: Dict) -> str:
    lines = []
    lines.append("=" * 62)
    lines.append("DECISION-QUALITY PATTERN REPORT (retrospective — not advice)")
    lines.append("=" * 62)

    n = summary["n"]
    if n == 0:
        lines.append("No graded trades yet. Run `export` -> Claude.ai -> `ingest`.")
        return "\n".join(lines)

    lines.append(f"graded trades: {n}")
    lines.append(f"total realized P&L: {summary['total_pnl']:+,.2f}")
    if summary["win_rate"] is not None:
        lines.append(f"win rate: {summary['win_rate'] * 100:.0f}%")
    if summary["avg_gpa"] is not None:
        lines.append(f"average process GPA (A=4..F=0): {summary['avg_gpa']:.2f}")
    dist = summary["grade_distribution"]
    lines.append("process grades: " + "  ".join(f"{k}:{dist[k]}" for k in sorted(dist)))

    lines.append("")
    lines.append("PROCESS vs LUCK")
    lines.append("-" * 62)
    attr = summary["attribution"]
    lines.append(f"  skill wins:     {attr.get('skill_win', 0)}")
    lines.append(f"  lucky wins:     {attr.get('luck_win', 0)}   <- don't reinforce")
    lines.append(f"  correct losses: {attr.get('correct_loss', 0)}   <- don't self-punish")
    lines.append(f"  process losses: {attr.get('process_loss', 0)}   <- FIX THESE")
    if attr.get("mixed"):
        lines.append(f"  mixed:          {attr['mixed']}")
    lines.append(f"  trades with NO invalidation defined: {summary['no_invalidation_count']}")

    if summary["top_biases"]:
        lines.append("")
        lines.append("TOP BIAS TAGS")
        lines.append("-" * 62)
        for b in summary["top_biases"]:
            lines.append(f"  {b['tag']:<32} x{b['count']}   P&L {b['pnl']:+,.2f}")

    lines.append("")
    lines.append("P&L BY REGIME FIT")
    lines.append("-" * 62)
    for k, v in sorted(summary["pnl_by_regime_fit"].items()):
        lines.append(f"  {k:<16} {v:+,.2f}")

    lines.append("")
    lines.append("P&L BY STRATEGY")
    lines.append("-" * 62)
    for k, v in sorted(summary["pnl_by_strategy"].items()):
        lines.append(f"  {k:<28} {v:+,.2f}")

    return "\n".join(lines)
