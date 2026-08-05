"""SQLite persistence. All writes are idempotent on trade_id."""

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional, Tuple

from .models import Trade

_SCHEMA = """
CREATE TABLE IF NOT EXISTS trades (
    trade_id     TEXT PRIMARY KEY,
    account_hash TEXT,
    underlying   TEXT,
    strategy     TEXT,
    legs         TEXT,
    opened_at    TEXT,
    closed_at    TEXT,
    entry_net    REAL,
    exit_net     REAL,
    realized_pnl REAL,
    quantity     INTEGER
);
CREATE TABLE IF NOT EXISTS contexts (
    trade_id     TEXT PRIMARY KEY,
    context_json TEXT,
    built_at     TEXT
);
CREATE TABLE IF NOT EXISTS theses (
    trade_id    TEXT PRIMARY KEY,
    thesis_text TEXT,
    conviction  INTEGER,
    recorded_at TEXT
);
CREATE TABLE IF NOT EXISTS gradings (
    trade_id   TEXT PRIMARY KEY,
    grade_json TEXT,
    model      TEXT,
    graded_at  TEXT
);
CREATE TABLE IF NOT EXISTS chain_snapshots (
    underlying TEXT,
    snapped_at TEXT,
    spot       REAL,
    chain_json TEXT,
    PRIMARY KEY (underlying, snapped_at)
);
"""


def _iso(dt) -> Optional[str]:
    if dt is None:
        return None
    if isinstance(dt, str):
        return dt
    return dt.isoformat()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Journal:
    def __init__(self, db_path):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(db_path))
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(_SCHEMA)
        self.conn.commit()

    def close(self):
        self.conn.close()

    # ---- trades ------------------------------------------------------------

    def upsert_trade(self, trade: Trade):
        self.conn.execute(
            """INSERT INTO trades (trade_id, account_hash, underlying, strategy, legs,
                                   opened_at, closed_at, entry_net, exit_net, realized_pnl, quantity)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(trade_id) DO UPDATE SET
                   account_hash=excluded.account_hash, underlying=excluded.underlying,
                   strategy=excluded.strategy, legs=excluded.legs,
                   opened_at=excluded.opened_at, closed_at=excluded.closed_at,
                   entry_net=excluded.entry_net, exit_net=excluded.exit_net,
                   realized_pnl=excluded.realized_pnl, quantity=excluded.quantity""",
            (
                trade.trade_id,
                trade.account_hash,
                trade.underlying,
                trade.strategy,
                trade.leg_summary(),
                _iso(trade.opened_at),
                _iso(trade.closed_at),
                trade.entry_net,
                trade.exit_net,
                trade.realized_pnl,
                trade.quantity,
            ),
        )
        self.conn.commit()

    def get_trade_row(self, trade_id: str) -> Optional[dict]:
        row = self.conn.execute("SELECT * FROM trades WHERE trade_id=?", (trade_id,)).fetchone()
        return dict(row) if row else None

    def closed_trades(self) -> List[dict]:
        rows = self.conn.execute(
            "SELECT * FROM trades WHERE closed_at IS NOT NULL ORDER BY opened_at"
        ).fetchall()
        return [dict(r) for r in rows]

    # ---- contexts ----------------------------------------------------------

    def save_context(self, trade_id: str, context: dict):
        self.conn.execute(
            """INSERT INTO contexts (trade_id, context_json, built_at) VALUES (?,?,?)
               ON CONFLICT(trade_id) DO UPDATE SET
                   context_json=excluded.context_json, built_at=excluded.built_at""",
            (trade_id, json.dumps(context, default=str), _now()),
        )
        self.conn.commit()

    def get_context(self, trade_id: str) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT context_json FROM contexts WHERE trade_id=?", (trade_id,)
        ).fetchone()
        return json.loads(row["context_json"]) if row else None

    # ---- theses ------------------------------------------------------------

    def save_thesis(self, trade_id: str, thesis_text: str, conviction: int):
        self.conn.execute(
            """INSERT INTO theses (trade_id, thesis_text, conviction, recorded_at) VALUES (?,?,?,?)
               ON CONFLICT(trade_id) DO UPDATE SET
                   thesis_text=excluded.thesis_text, conviction=excluded.conviction,
                   recorded_at=excluded.recorded_at""",
            (trade_id, thesis_text, conviction, _now()),
        )
        self.conn.commit()

    def get_thesis(self, trade_id: str) -> Optional[dict]:
        row = self.conn.execute("SELECT * FROM theses WHERE trade_id=?", (trade_id,)).fetchone()
        return dict(row) if row else None

    def trades_without_thesis(self) -> List[dict]:
        rows = self.conn.execute(
            """SELECT t.* FROM trades t
               LEFT JOIN theses th ON th.trade_id = t.trade_id
               WHERE th.trade_id IS NULL ORDER BY t.opened_at"""
        ).fetchall()
        return [dict(r) for r in rows]

    # ---- gradings ----------------------------------------------------------

    def save_grade(self, trade_id: str, grade: dict, model: str):
        self.conn.execute(
            """INSERT INTO gradings (trade_id, grade_json, model, graded_at) VALUES (?,?,?,?)
               ON CONFLICT(trade_id) DO UPDATE SET
                   grade_json=excluded.grade_json, model=excluded.model,
                   graded_at=excluded.graded_at""",
            (trade_id, json.dumps(grade), model, _now()),
        )
        self.conn.commit()

    def get_grade(self, trade_id: str) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT grade_json FROM gradings WHERE trade_id=?", (trade_id,)
        ).fetchone()
        return json.loads(row["grade_json"]) if row else None

    def gradeable_trades(self) -> List[dict]:
        """Closed AND has a thesis AND not yet graded."""
        rows = self.conn.execute(
            """SELECT t.* FROM trades t
               JOIN theses th ON th.trade_id = t.trade_id
               LEFT JOIN gradings g ON g.trade_id = t.trade_id
               WHERE t.closed_at IS NOT NULL AND g.trade_id IS NULL
               ORDER BY t.opened_at"""
        ).fetchall()
        return [dict(r) for r in rows]

    def all_grades(self) -> List[dict]:
        """Each item: trade row fields + parsed 'grade' dict + grading metadata."""
        rows = self.conn.execute(
            """SELECT t.*, g.grade_json, g.model, g.graded_at FROM gradings g
               JOIN trades t ON t.trade_id = g.trade_id ORDER BY t.opened_at"""
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["grade"] = json.loads(d.pop("grade_json"))
            out.append(d)
        return out

    # ---- chain snapshots ---------------------------------------------------

    def save_chain_snapshot(self, underlying: str, snapped_at, spot, chain: dict):
        self.conn.execute(
            """INSERT INTO chain_snapshots (underlying, snapped_at, spot, chain_json)
               VALUES (?,?,?,?)
               ON CONFLICT(underlying, snapped_at) DO UPDATE SET
                   spot=excluded.spot, chain_json=excluded.chain_json""",
            (underlying, _iso(snapped_at), spot, json.dumps(chain, default=str)),
        )
        self.conn.commit()

    def nearest_chain(self, underlying: str, when) -> Optional[Tuple[Optional[float], dict]]:
        """(spot, chain) for the snapshot within 2 days of `when`, else None."""
        if isinstance(when, str):
            when = datetime.fromisoformat(when.replace("Z", "+00:00"))
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        rows = self.conn.execute(
            "SELECT snapped_at, spot, chain_json FROM chain_snapshots WHERE underlying=?",
            (underlying,),
        ).fetchall()
        best = None
        for r in rows:
            snapped = datetime.fromisoformat(r["snapped_at"].replace("Z", "+00:00"))
            if snapped.tzinfo is None:
                snapped = snapped.replace(tzinfo=timezone.utc)
            dist = abs(snapped - when)
            if dist <= timedelta(days=2) and (best is None or dist < best[0]):
                best = (dist, r)
        if best is None:
            return None
        r = best[1]
        return (r["spot"], json.loads(r["chain_json"]))
