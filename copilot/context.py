"""Reconstruct the market context that existed at a trade's entry.

Regime + realized vol always come from Schwab price history. GEX / IV come from
a chain snapshot ONLY if one exists near the entry timestamp — Schwab serves no
historical chains, so we never fabricate option-derived context.
"""

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Protocol, Tuple

from .models import Trade
from .regime import RegimeSeries, realized_vol

_BAD_IV = (None, -999, -999.0)


@dataclass
class MarketContext:
    regime_state: str = "unknown"
    regime_detail: str = "unknown"
    spot_entry: str = "unknown"
    rv20: str = "unknown"
    iv_context: str = "unknown"
    term_structure: str = "unknown"
    gex_context: str = "unknown"
    flow_context: str = "unknown"
    notable: str = "unknown"
    raw: dict = field(default_factory=dict)


class ChainProvider(Protocol):
    def chain_as_of(self, underlying: str, when) -> Optional[dict]: ...

    def spot_as_of(self, underlying: str, when) -> Optional[float]: ...


def compute_gex(chain: dict, spot: Optional[float]) -> Optional[float]:
    """Naive dealer-gamma proxy: spot^2 * 1% * 100 * sum(gamma*OI_call - gamma*OI_put).

    Sign > 0 => dealers long gamma (pinning / mean-reversion pressure);
    sign < 0 => dealers short gamma (moves can accelerate / trend).
    """
    if spot is None:
        return None
    total = 0.0
    used = 0
    for map_name, sign in (("callExpDateMap", 1.0), ("putExpDateMap", -1.0)):
        for strikes in (chain.get(map_name) or {}).values():
            for contracts in strikes.values():
                for c in contracts:
                    gamma = c.get("gamma")
                    oi = c.get("openInterest")
                    if gamma is None or oi is None:
                        continue
                    gamma = float(gamma)
                    oi = float(oi)
                    if not math.isfinite(gamma) or gamma == -999.0:
                        continue
                    total += sign * gamma * oi
                    used += 1
    if used == 0:
        return None
    return spot * spot * 0.01 * 100 * total


def summarize_iv(chain: dict) -> Tuple[Optional[str], Optional[str]]:
    """Nearest-to-ATM IV per expiry -> (front-expiry IV summary, term-structure label)."""
    spot = chain.get("underlyingPrice")
    if spot is None:
        return None, None

    per_expiry: Dict[str, Tuple[int, float]] = {}  # "YYYY-MM-DD" -> (dte, atm_iv)
    for map_name in ("callExpDateMap", "putExpDateMap"):
        for exp_key, strikes in (chain.get(map_name) or {}).items():
            try:
                exp_date, dte = exp_key.split(":")
                dte = int(dte)
            except ValueError:
                continue
            best: Optional[Tuple[float, float]] = None  # (strike distance, iv)
            for strike_str, contracts in strikes.items():
                for c in contracts:
                    iv = c.get("volatility")
                    if iv in _BAD_IV:
                        continue
                    dist = abs(float(strike_str) - float(spot))
                    if best is None or dist < best[0]:
                        best = (dist, float(iv))
            if best is not None and exp_date not in per_expiry:
                per_expiry[exp_date] = (dte, best[1])

    if not per_expiry:
        return None, None

    ordered = sorted(per_expiry.values(), key=lambda t: t[0])
    front_dte, front_iv = ordered[0]
    iv_context = f"front-expiry ATM IV {front_iv:.1f}% ({front_dte} DTE)"

    term_structure = None
    if len(ordered) >= 2:
        back_iv = ordered[-1][1]
        diff = back_iv - front_iv
        if diff > 0.5:
            term_structure = "contango"
        elif diff < -0.5:
            term_structure = "backwardation"
        else:
            term_structure = "flat"
        term_structure += f" (back-front ATM IV {diff:+.1f} pts)"
    return iv_context, term_structure


def build_context(
    trade: Trade,
    regime: Optional[RegimeSeries],
    spy_candles: List[dict],
    chain_provider: Optional[ChainProvider],
) -> MarketContext:
    ctx = MarketContext()

    if regime is not None and trade.opened_at is not None:
        r = regime.as_of(trade.opened_at)
        ctx.regime_state = r["state"]
        ctx.regime_detail = r["detail"]

    if spy_candles and trade.opened_at is not None:
        rv = realized_vol(spy_candles, trade.opened_at)
        if rv is not None:
            ctx.rv20 = f"{rv * 100:.1f}% (annualized, SPY)"

    if chain_provider is not None and trade.opened_at is not None:
        spot = chain_provider.spot_as_of(trade.underlying, trade.opened_at)
        if spot is not None:
            ctx.spot_entry = f"{spot:g}"
        chain = chain_provider.chain_as_of(trade.underlying, trade.opened_at)
        if chain is not None:
            if spot is None:
                spot = chain.get("underlyingPrice")
                if spot is not None:
                    ctx.spot_entry = f"{spot:g}"
            gex = compute_gex(chain, spot)
            if gex is not None:
                lean = (
                    "positive gamma (pinning/mean-revert pressure)"
                    if gex > 0
                    else "negative gamma (moves can accelerate/trend)"
                )
                ctx.gex_context = f"GEX ~ {gex:,.0f} ($ notional per 1% move); {lean}"
                ctx.raw["gex"] = gex
            iv_context, term_structure = summarize_iv(chain)
            if iv_context is not None:
                ctx.iv_context = iv_context
            if term_structure is not None:
                ctx.term_structure = term_structure
    return ctx
