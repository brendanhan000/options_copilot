# options_copilot

A personal, local, **retrospective** options-trading decision journal.

**Run:** start the Schwab hub (`../schwab_hub/run.sh`), then `python run.py sync --days 365` — full steps in `SETUP.md`.

It pulls your own fills from the Schwab Trader API, reconstructs the market
context that existed at each trade's entry (volatility regime, realized vol,
dealer-gamma and IV reads when a chain snapshot is available), lets you record
the thesis you had at entry, and then grades the **decision quality** — skill
vs. luck — using Claude. Grading is free by default via a copy-paste loop with
Claude.ai; an optional paid path uses the Anthropic API (`claude-fable-5`).

**Retrospective only.** This tool analyzes trades you have already closed. It
never emits a forward buy/sell recommendation, and the grading prompt
explicitly forbids the model from doing so. Nothing here is investment advice.

## How it works

1. `sync` — pulls TRADE transactions from Schwab, reconstructs round-trip
   trades and realized P&L, snapshots current option chains, fits a 3-state
   HMM volatility regime on SPY, and stores the entry context per trade.
2. `thesis` — you record what you were thinking at entry (and a 1–5 conviction).
3. `export` — writes one self-contained grading prompt per trade to
   `data/grading_queue/<trade_id>.txt`. Paste each into Claude.ai.
4. `ingest` — save each JSON reply as `<trade_id>.json` in the same folder and
   ingest them. (Or skip 3–4 with `grade` if you set `ANTHROPIC_API_KEY`.)
5. `patterns` — aggregate report: process GPA, skill-vs-luck tally, bias tags,
   P&L by regime fit and by strategy.

## Architecture

| Module | Role |
| --- | --- |
| `copilot/config.py` | .env loading, paths under `data/`, `require()` |
| `copilot/prompts.py` | grading system prompt + user template (the core contract) |
| `copilot/schwab_source.py` | Schwab Trader API via the central schwab_hub (transactions, price history, chains) |
| `copilot/models.py` | `Instrument`, `Fill`, `Trade`, `classify_strategy()` |
| `copilot/trade_builder.py` | transactions → round-trip trades + realized P&L |
| `copilot/regime.py` | 3-state Gaussian HMM regime + realized vol |
| `copilot/context.py` | entry-context reconstruction; GEX + IV from chain snapshots |
| `copilot/journal.py` | SQLite store (trades, contexts, theses, grades, chain snapshots) |
| `copilot/grader.py` | prompt rendering; manual-prompt builder (free) + API grader (paid) |
| `copilot/analyze.py` | aggregate grades → pattern report |
| `copilot/cli.py` | `sync` / `thesis` / `export` / `ingest` / `grade` / `patterns` / `show` |

## Honest data limitation

Schwab serves **no historical option chains**. The chain snapshot taken during
`sync` reflects the market *now*, so GEX / IV / term-structure context is only
accurate for trades opened within ~2 days of a sync. For older trades those
fields are left `"unknown"` — never fabricated — and the grader is instructed
to lower its confidence accordingly. (A `POLYGON_API_KEY` slot exists if you
ever want to backfill historical context from a paid data source.)

Also note Schwab's OAuth policy: the refresh token expires after **7 days** with
no programmatic renewal. The schwab_hub owns it, so when a `sync` fails with an
auth error, run `../schwab_hub/run.sh login` (this repo never opens a browser
login itself). That is normal.

## Setup

See [SETUP.md](SETUP.md). Secrets live only in `.env` (gitignored) and, for Schwab, in the hub; nothing is
hard-coded and no data leaves your machine except the prompts you choose to
paste into Claude.ai or send to the Anthropic API.
