"""3-state Gaussian HMM volatility regime on daily log-returns, plus realized vol.

States are RELABELED by ascending volatility so the mapping is stable across
fits: 0 -> calm, 1 -> normal, 2 -> stress. `as_of` is causal — it uses the last
observation on/before the requested date, never anything after it.
"""

from bisect import bisect_right
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Dict, List, Optional

import numpy as np

TRADING_DAYS = 252

_LABELS_3 = ["calm", "normal", "stress"]


def _candle_dates_closes(candles):
    candles = sorted(candles, key=lambda c: c["datetime"])
    dates = [datetime.fromtimestamp(c["datetime"] / 1000, tz=timezone.utc).date() for c in candles]
    closes = np.array([float(c["close"]) for c in candles])
    return dates, closes


@dataclass
class RegimeSeries:
    dates: List[date]  # aligned to returns (first candle date dropped)
    states: List[int]  # relabeled: ascending volatility
    probs: np.ndarray  # (T, n_states) smoothed posteriors, columns relabeled
    means: List[float]  # per-state mean daily log-return
    vols: List[float]  # per-state ANNUALIZED vol
    label_map: Dict[int, str]

    def as_of(self, when) -> Dict[str, str]:
        if isinstance(when, datetime):
            when = when.date()
        i = bisect_right(self.dates, when) - 1
        if i < 0:
            return {"state": "unknown", "detail": "no regime history on/before this date"}
        s = self.states[i]
        p = self.probs[i]
        probs_txt = ", ".join(
            f"{self.label_map[j]}={p[j]:.2f}" for j in range(len(p))
        )
        detail = (
            f"as of {self.dates[i].isoformat()}: ann vol ~{self.vols[s] * 100:.0f}%, "
            f"mean daily {self.means[s] * 100:+.3f}%; posteriors: {probs_txt}"
        )
        return {"state": self.label_map[s], "detail": detail}


def fit_regime(candles: List[dict], n_states: int = 3) -> RegimeSeries:
    from hmmlearn.hmm import GaussianHMM

    dates, closes = _candle_dates_closes(candles)
    returns = np.diff(np.log(closes))
    X = returns.reshape(-1, 1)

    model = GaussianHMM(
        n_components=n_states, covariance_type="diag", n_iter=200, random_state=42
    )
    model.fit(X)
    raw_states = model.predict(X)  # Viterbi
    raw_probs = model.predict_proba(X)  # smoothed posteriors

    daily_vols = np.sqrt(np.asarray(model.covars_).reshape(n_states))
    order = np.argsort(daily_vols)  # ascending vol; order[rank] = raw state index
    rank_of = {int(raw): rank for rank, raw in enumerate(order)}

    states = [rank_of[int(s)] for s in raw_states]
    probs = raw_probs[:, order]
    means = [float(np.asarray(model.means_).reshape(-1)[raw]) for raw in order]
    vols = [float(daily_vols[raw] * np.sqrt(TRADING_DAYS)) for raw in order]

    if n_states == 3:
        label_map = dict(enumerate(_LABELS_3))
    else:
        label_map = {k: f"vol_rank_{k}" for k in range(n_states)}

    return RegimeSeries(
        dates=dates[1:],  # aligned to returns
        states=states,
        probs=probs,
        means=means,
        vols=vols,
        label_map=label_map,
    )


def realized_vol(candles: List[dict], as_of, window: int = 20) -> Optional[float]:
    """Annualized std of the last `window` daily log-returns ending on/before as_of."""
    if isinstance(as_of, datetime):
        as_of = as_of.date()
    dates, closes = _candle_dates_closes(candles)
    n = bisect_right(dates, as_of)
    if n < window + 1:
        return None
    returns = np.diff(np.log(closes[:n]))[-window:]
    return float(np.std(returns, ddof=1) * np.sqrt(TRADING_DAYS))
