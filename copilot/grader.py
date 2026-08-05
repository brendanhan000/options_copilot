"""Render grading prompts and parse grade JSON.

The FREE path needs no API key: build_manual_prompt() produces a self-contained
block you paste into Claude.ai. The Grader class is the optional paid path.
"""

import json
import re
from datetime import datetime
from typing import Optional

from . import config
from .prompts import SYSTEM_PROMPT, USER_TEMPLATE


def _ctx_get(context, key: str, default: str = "unknown"):
    if context is None:
        return default
    if isinstance(context, dict):
        val = context.get(key)
    else:
        val = getattr(context, key, None)
    return val if val not in (None, "") else default


def _holding_period(row: dict) -> str:
    opened, closed = row.get("opened_at"), row.get("closed_at")
    if not opened or not closed:
        return "open"
    o = datetime.fromisoformat(str(opened).replace("Z", "+00:00"))
    c = datetime.fromisoformat(str(closed).replace("Z", "+00:00"))
    delta = c - o
    return f"{delta.days}d {delta.seconds // 3600}h"


def render_user_message(trade_row: dict, context, thesis) -> str:
    """Fill USER_TEMPLATE from a trade row, its context, and its thesis.

    Module-level on purpose: the free export path uses it with no API key.
    """
    thesis_text = "not recorded"
    if thesis:
        if isinstance(thesis, dict):
            thesis_text = thesis.get("thesis_text") or "not recorded"
            conviction = thesis.get("conviction")
        else:
            thesis_text, conviction = str(thesis), None
        if conviction is not None:
            thesis_text = f"{thesis_text}\n(stated conviction: {conviction}/5)"

    entry_net = trade_row.get("entry_net")
    realized_pnl = trade_row.get("realized_pnl")
    pnl_pct = "unknown"
    if entry_net and realized_pnl is not None:
        pnl_pct = f"{realized_pnl / abs(entry_net) * 100:+.1f}%"

    return USER_TEMPLATE.format(
        trade_id=trade_row.get("trade_id"),
        underlying=trade_row.get("underlying"),
        strategy=trade_row.get("strategy"),
        legs=trade_row.get("legs") or "unknown",
        opened_at=trade_row.get("opened_at") or "unknown",
        closed_at=trade_row.get("closed_at") or "still open",
        holding_period=_holding_period(trade_row),
        entry_net=entry_net,
        exit_net=trade_row.get("exit_net"),
        quantity=trade_row.get("quantity"),
        thesis_text=thesis_text,
        regime_state=_ctx_get(context, "regime_state"),
        regime_detail=_ctx_get(context, "regime_detail"),
        spot_entry=_ctx_get(context, "spot_entry"),
        rv20=_ctx_get(context, "rv20"),
        iv_context=_ctx_get(context, "iv_context"),
        term_structure=_ctx_get(context, "term_structure"),
        gex_context=_ctx_get(context, "gex_context"),
        flow_context=_ctx_get(context, "flow_context"),
        notable_context=_ctx_get(context, "notable"),
        realized_pnl=realized_pnl,
        realized_pnl_pct=pnl_pct,
        mfe=_ctx_get(context, "mfe"),
        mae=_ctx_get(context, "mae"),
        spot_exit=_ctx_get(context, "spot_exit"),
        path_notes=_ctx_get(context, "path_notes"),
    )


def build_manual_prompt(trade_row: dict, context, thesis) -> str:
    """The self-contained block to paste into Claude.ai (free path)."""
    return SYSTEM_PROMPT + "\n\n---\n\n" + render_user_message(trade_row, context, thesis)


def _parse_json(text: str) -> dict:
    """Extract the JSON object even if wrapped in ```json fences or prose."""
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z]*\s*", "", t)
        t = re.sub(r"\s*```\s*$", "", t)
    start = t.find("{")
    end = t.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("no JSON object found in text")
    return json.loads(t[start : end + 1])


class Grader:
    """Optional paid path via the Anthropic API. Requires ANTHROPIC_API_KEY."""

    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None):
        api_key = api_key or config.ANTHROPIC_API_KEY
        if not api_key:
            raise SystemExit(
                "ANTHROPIC_API_KEY is not set. The API grader is optional — "
                "use `export`/`ingest` for the free Claude.ai copy-paste loop."
            )
        import anthropic

        self.client = anthropic.Anthropic(api_key=api_key)
        self.model = model or config.GRADER_MODEL

    def grade(self, trade_row: dict, context, thesis) -> dict:
        resp = self.client.messages.create(
            model=self.model,
            max_tokens=config.GRADER_MAX_TOKENS,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": render_user_message(trade_row, context, thesis)}],
        )
        text = "".join(block.text for block in resp.content if getattr(block, "type", "") == "text")
        return _parse_json(text)
