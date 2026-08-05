"""Turn raw Schwab transactions into Fills, then into round-trip Trades.

Sign conventions (correctness-critical):
  cost       = SIGNED cash for the item (buy -> negative)
  amount     = fill quantity; live Schwab payloads sign it (sell -> negative),
               so direction comes from cost's sign, not amount's:
  signed_qty = +|amount| on a buy (cost < 0), -|amount| on a sell (cost > 0);
               when cost == 0 (e.g. expiration removal) trust amount's own sign
  net_amount = cost
A round trip closes when every per-symbol exposure returns to ~0. Expirations
and assignments arrive as RECEIVE_AND_DELIVER (cost 0, effect CLOSING) and are
parsed as fills too — without them, expired positions would never close.
"""

from collections import defaultdict
from datetime import date, datetime, timezone
from typing import Dict, List, Optional

from .models import Fill, Instrument, Right, Trade, classify_strategy

_EPS = 1e-9


def _parse_when(txn: dict) -> Optional[datetime]:
    raw = txn.get("tradeDate") or txn.get("time")
    if not raw:
        return None
    dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _parse_date(raw) -> Optional[date]:
    if not raw:
        return None
    return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).date()


def _parse_instrument(ins: dict) -> Optional[Instrument]:
    asset_type = (ins.get("assetType") or "").upper()
    symbol = ins.get("symbol") or ""
    if asset_type == "OPTION":
        put_call = (ins.get("putCall") or "").upper()
        right = Right.PUT if put_call == "PUT" else Right.CALL
        underlying = ins.get("underlyingSymbol") or symbol.split()[0] or symbol
        strike = ins.get("strikePrice")
        return Instrument(
            symbol=symbol,
            underlying=underlying,
            asset_type="OPTION",
            right=right,
            strike=float(strike) if strike is not None else None,
            expiry=_parse_date(ins.get("expirationDate")),
        )
    if asset_type == "EQUITY":
        return Instrument(symbol=symbol, underlying=symbol, asset_type="EQUITY", right=Right.EQUITY)
    return None


def parse_fills(transactions: List[dict], account_hash: str) -> List[Fill]:
    fills: List[Fill] = []
    for txn in transactions or []:
        if (txn.get("type") or "").upper() not in ("TRADE", "RECEIVE_AND_DELIVER"):
            continue
        txn_id = str(txn.get("activityId") or txn.get("transactionId") or txn.get("orderId") or "")
        when = _parse_when(txn)
        if when is None:
            continue
        items = txn.get("transferItems") or []
        trade_items = []
        fee_total = 0.0
        for item in items:
            ins = item.get("instrument") or {}
            if (ins.get("assetType") or "").upper() in ("OPTION", "EQUITY"):
                trade_items.append(item)
            elif item.get("feeType"):
                fee_total += abs(float(item.get("cost") or 0.0))
        fee_each = fee_total / len(trade_items) if trade_items else 0.0
        for item in trade_items:
            instrument = _parse_instrument(item.get("instrument") or {})
            if instrument is None:
                continue
            amount = float(item["amount"])  # may arrive signed or as a magnitude
            cost = float(item["cost"])  # SIGNED cash (buy -> negative)
            effect = (item.get("positionEffect") or "").upper()
            if cost < 0:
                signed_qty = abs(amount)  # buy = +long
            elif cost > 0:
                signed_qty = -abs(amount)  # sell = -short
            else:
                signed_qty = amount  # zero-cost (expiration/assignment): amount is signed
            fills.append(
                Fill(
                    txn_id=txn_id,
                    account_hash=account_hash,
                    instrument=instrument,
                    when=when,
                    quantity=signed_qty,
                    price=float(item.get("price") or 0.0),
                    opening=(effect == "OPENING"),
                    net_amount=cost,
                    fees=fee_each,
                )
            )
    # At identical timestamps, process closings before openings so a
    # close-and-reopen in the same second slices into two round trips.
    fills.sort(key=lambda f: (f.when, f.opening))
    return fills


def _finalize(underlying: str, legs: List[Fill], closed: bool) -> Trade:
    opened_at = legs[0].when
    closed_at = legs[-1].when if closed else None
    entry_net = round(sum(l.net_amount for l in legs if l.opening), 2)
    exit_net = round(sum(l.net_amount for l in legs if not l.opening), 2)
    realized_pnl = round(entry_net + exit_net, 2) if closed else 0.0
    opening_opts = [abs(l.quantity) for l in legs if l.opening and l.instrument.is_option]
    if opening_opts:
        quantity = int(max(opening_opts))
    else:
        quantity = int(max((abs(l.quantity) for l in legs), default=0))
    trade_id = f"{underlying}-{opened_at.strftime('%Y%m%d%H%M%S')}-{legs[0].txn_id}"
    return Trade(
        trade_id=trade_id,
        account_hash=legs[0].account_hash,
        underlying=underlying,
        legs=list(legs),
        opened_at=opened_at,
        closed_at=closed_at,
        strategy=classify_strategy(legs),
        entry_net=entry_net,
        exit_net=exit_net,
        realized_pnl=realized_pnl,
        quantity=quantity,
    )


def build_trades(fills: List[Fill]) -> List[Trade]:
    by_underlying: Dict[str, List[Fill]] = defaultdict(list)
    for f in fills:
        by_underlying[f.instrument.underlying].append(f)

    trades: List[Trade] = []
    for underlying, group in by_underlying.items():
        group.sort(key=lambda f: (f.when, f.opening))
        exposure: Dict[str, float] = {}
        legs: List[Fill] = []
        for f in group:
            legs.append(f)
            exposure[f.instrument.symbol] = exposure.get(f.instrument.symbol, 0.0) + f.quantity
            if all(abs(v) < _EPS for v in exposure.values()):
                trades.append(_finalize(underlying, legs, closed=True))
                legs = []
                exposure = {}
        if legs:
            trades.append(_finalize(underlying, legs, closed=False))  # still open
    trades.sort(key=lambda t: t.opened_at)
    return trades
