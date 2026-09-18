"""Schwab Trader API access via the central schwab_hub.

Auth: none here. The hub (../schwab_hub/run.sh) owns the Schwab token; the
refresh token lives 7 days with NO programmatic renewal, so the weekly login is
`../schwab_hub/run.sh login`. That is Schwab policy, not a bug.

Account access is indirect: get_account_numbers() maps each accountNumber to a
hashValue, and every subsequent call uses the hash.
"""

from datetime import datetime, timedelta
from typing import List, Optional

from . import config

# Schwab caps each transactions query near 1 year.
_PAGE_DAYS = 360


def make_client():
    try:
        from schwab_hub_client import HubClient
    except ImportError as exc:
        raise RuntimeError("schwab_hub_client missing: pip install -e ../schwab_hub") from exc
    return HubClient()


class SchwabSource:
    def __init__(self, client=None):
        self.client = client or make_client()

    def account_hashes(self) -> List[str]:
        resp = self.client.get_account_numbers()
        resp.raise_for_status()
        return [acct["hashValue"] for acct in resp.json()]

    def transactions(self, account_hash: str, start: datetime, end: datetime) -> List[dict]:
        """TRADE + RECEIVE_AND_DELIVER transactions over [start, end], paged by year."""
        # RECEIVE_AND_DELIVER is needed alongside TRADE: option expirations and
        # assignments arrive under it, and without them positions closed that way
        # would look open forever.
        txn_types = ["TRADE", "RECEIVE_AND_DELIVER"]
        txns: List[dict] = []
        page_start = start
        while page_start < end:
            page_end = min(page_start + timedelta(days=_PAGE_DAYS), end)
            resp = self.client.get_transactions(
                account_hash, start_date=page_start, end_date=page_end, transaction_types=txn_types
            )
            resp.raise_for_status()
            data = resp.json()
            if isinstance(data, list):
                txns.extend(data)
            page_start = page_end
        return txns

    def price_history(self, symbol: str, start: datetime, end: datetime) -> List[dict]:
        resp = self.client.get_price_history_every_day(
            symbol, start_datetime=start, end_datetime=end
        )
        resp.raise_for_status()
        return resp.json().get("candles", [])

    def option_chain(self, symbol: str) -> Optional[dict]:
        """CURRENT chain only — Schwab serves no historical chains."""
        resp = self.client.get_option_chain(symbol)
        resp.raise_for_status()
        chain = resp.json()
        if not chain.get("callExpDateMap") and not chain.get("putExpDateMap"):
            return None
        return chain
