"""Core data shapes: Instrument, Fill, Trade, and strategy classification."""

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import List, Optional


class Right(Enum):
    CALL = "CALL"
    PUT = "PUT"
    EQUITY = "EQUITY"


@dataclass(frozen=True)
class Instrument:
    symbol: str
    underlying: str
    asset_type: str  # "OPTION" or "EQUITY"
    right: Right
    strike: Optional[float] = None
    expiry: Optional[date] = None

    @property
    def is_option(self) -> bool:
        return self.asset_type == "OPTION"


@dataclass
class Fill:
    txn_id: str
    account_hash: str
    instrument: Instrument
    when: datetime  # tz-aware UTC
    quantity: float  # SIGNED: + long / - short
    price: float
    opening: bool  # positionEffect == "OPENING"
    net_amount: float  # SIGNED cash (buy -> negative)
    fees: float = 0.0


def _fmt_qty(q: float) -> str:
    q = abs(q)
    return str(int(q)) if q == int(q) else f"{q:g}"


def _fmt_strike(s: float) -> str:
    return str(int(s)) if s == int(s) else f"{s:g}"


@dataclass
class Trade:
    trade_id: str
    account_hash: str
    underlying: str
    legs: List[Fill] = field(default_factory=list)
    opened_at: Optional[datetime] = None
    closed_at: Optional[datetime] = None
    strategy: str = ""
    entry_net: float = 0.0
    exit_net: float = 0.0
    realized_pnl: float = 0.0
    quantity: int = 0  # dominant option leg size

    def holding_period(self) -> str:
        if self.closed_at is None or self.opened_at is None:
            return "open"
        delta = self.closed_at - self.opened_at
        return f"{delta.days}d {delta.seconds // 3600}h"

    def leg_summary(self) -> str:
        parts = []
        source = [l for l in self.legs if l.opening] or self.legs
        for l in source:
            side = "long" if l.quantity > 0 else "short"
            ins = l.instrument
            if ins.is_option:
                r = "C" if ins.right == Right.CALL else "P"
                expiry = ins.expiry.isoformat() if ins.expiry else "?"
                strike = _fmt_strike(ins.strike) if ins.strike is not None else "?"
                parts.append(f"{side} {_fmt_qty(l.quantity)}x {ins.underlying} {expiry} {strike}{r}")
            else:
                parts.append(f"{side} {_fmt_qty(l.quantity)}x {ins.symbol}")
        return "; ".join(parts)


def classify_strategy(legs: List[Fill]) -> str:
    """Classify from the OPENING legs only."""
    opening = [l for l in legs if l.opening]
    opts = [l for l in opening if l.instrument.is_option]
    if not opts:
        return "equity"
    n = len(opts)
    rights = {l.instrument.right for l in opts}
    if n == 1:
        leg = opts[0]
        side = "long" if leg.quantity > 0 else "short"
        r = "call" if leg.instrument.right == Right.CALL else "put"
        return f"{side}_{r}"
    if n == 2:
        if len(rights) == 1:
            has_long = any(l.quantity > 0 for l in opts)
            has_short = any(l.quantity < 0 for l in opts)
            if has_long and has_short:
                return "call_vertical" if Right.CALL in rights else "put_vertical"
        else:
            if all(l.quantity > 0 for l in opts):
                return "long_strangle_or_straddle"
            if all(l.quantity < 0 for l in opts):
                return "short_strangle_or_straddle"
    if n == 4 and len(rights) == 2:
        return "iron_condor_or_fly"
    if n == 3 and len(rights) == 1:
        return "butterfly"
    return f"multi_leg_{n}"
