"""Environment/config loading. Secrets come from .env only — never hard-code."""

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

try:
    from dotenv import load_dotenv

    load_dotenv(PROJECT_ROOT / ".env")
except ImportError:
    pass


ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
GRADER_MODEL = os.getenv("GRADER_MODEL", "claude-fable-5")
GRADER_MAX_TOKENS = int(os.getenv("GRADER_MAX_TOKENS", "1500"))

POLYGON_API_KEY = os.getenv("POLYGON_API_KEY", "")  # optional; unused unless backfilling

DB_PATH = Path(os.getenv("COPILOT_DB_PATH", str(DATA_DIR / "journal.sqlite3")))

REGIME_SYMBOL = os.getenv("REGIME_SYMBOL", "SPY")
REGIME_STATES = int(os.getenv("REGIME_STATES", "3"))
REGIME_LOOKBACK_DAYS = int(os.getenv("REGIME_LOOKBACK_DAYS", "1500"))


def require(*names):
    """Exit with a clear message if any named config value is missing/empty."""
    missing = [n for n in names if not globals().get(n)]
    if missing:
        raise SystemExit(
            "Missing required configuration: "
            + ", ".join(missing)
            + "\nSet these in a .env file at the project root (see .env.example)."
        )
