"""options_copilot — a retrospective options-trading decision journal.

Pulls your own fills from the Schwab Trader API, reconstructs the market
context at each trade's entry, records your thesis, and grades DECISION
QUALITY (skill vs. luck) with Claude — free via a copy-paste loop with
Claude.ai, or optionally via the paid Anthropic API.

Retrospective only: this tool never emits forward trade recommendations.
"""

__version__ = "0.1.0"
