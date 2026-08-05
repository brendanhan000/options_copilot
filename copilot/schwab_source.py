"""Schwab Trader API access via schwab-py.

Auth: schwab-py's easy_client handles the whole OAuth dance — it runs a
temporary HTTPS loopback server on the callback port, does the token exchange,
and auto-refreshes the 30-minute access token. The refresh token lives 7 days
with NO programmatic renewal: after 7 idle days the next run re-opens the
browser login. That is Schwab policy, not a bug.

Account access is indirect: get_account_numbers() maps each accountNumber to a
hashValue, and every subsequent call uses the hash.
"""

from datetime import datetime, timedelta
from typing import List, Optional

from . import config

# Schwab caps each transactions query near 1 year.
_PAGE_DAYS = 360


def _txn_types():
    """Import the transaction-type enums defensively across schwab-py versions.

    RECEIVE_AND_DELIVER is needed alongside TRADE: option expirations and
    assignments arrive under it, and without them positions closed that way
    would look open forever.
    """
    try:
        from schwab.client import Client

        enum = Client.Transactions.TransactionType
    except (ImportError, AttributeError):
        return None
    types = [enum.TRADE]
    if hasattr(enum, "RECEIVE_AND_DELIVER"):
        types.append(enum.RECEIVE_AND_DELIVER)
    return types


def make_client():
    config.require("SCHWAB_APP_KEY", "SCHWAB_APP_SECRET")
    from schwab.auth import easy_client

    return easy_client(
        api_key=config.SCHWAB_APP_KEY,
        app_secret=config.SCHWAB_APP_SECRET,
        callback_url=config.SCHWAB_CALLBACK_URL,
        token_path=str(config.SCHWAB_TOKEN_PATH),
    )


class SchwabSource:
    def __init__(self, client=None):
        self.client = client or make_client()

    def account_hashes(self) -> List[str]:
        resp = self.client.get_account_numbers()
        resp.raise_for_status()
        return [acct["hashValue"] for acct in resp.json()]

    def transactions(self, account_hash: str, start: datetime, end: datetime) -> List[dict]:
        """TRADE + RECEIVE_AND_DELIVER transactions over [start, end], paged by year."""
        txn_types = _txn_types()
        txns: List[dict] = []
        page_start = start
        while page_start < end:
            page_end = min(page_start + timedelta(days=_PAGE_DAYS), end)
            kwargs = {"start_date": page_start, "end_date": page_end}
            if txn_types is not None:
                kwargs["transaction_types"] = txn_types
            resp = self.client.get_transactions(account_hash, **kwargs)
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
