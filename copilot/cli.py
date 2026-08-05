"""CLI: sync / thesis / export / ingest / grade / patterns / show."""

import argparse
import json
import sys
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from typing import Optional

from . import config
from .analyze import format_report, summarize
from .context import build_context
from .grader import Grader, _parse_json, build_manual_prompt
from .journal import Journal
from .trade_builder import build_trades, parse_fills


class JournalChainProvider:
    """ChainProvider backed by chain snapshots stored in the journal."""

    def __init__(self, journal: Journal):
        self.journal = journal

    def chain_as_of(self, underlying: str, when) -> Optional[dict]:
        hit = self.journal.nearest_chain(underlying, when)
        return hit[1] if hit else None

    def spot_as_of(self, underlying: str, when) -> Optional[float]:
        hit = self.journal.nearest_chain(underlying, when)
        return hit[0] if hit else None


def _queue_dir():
    d = config.DATA_DIR / "grading_queue"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _print_trade_line(row: dict):
    closed = row["closed_at"] or "OPEN"
    print(
        f"  {row['trade_id']}  {row['underlying']:<6} {row['strategy']:<24} "
        f"opened {row['opened_at']}  closed {closed}  pnl {row['realized_pnl']:+,.2f}"
    )


# ---- commands ---------------------------------------------------------------


def cmd_sync(args, journal: Journal):
    from .regime import fit_regime
    from .schwab_source import SchwabSource

    src = SchwabSource()
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=args.days)

    all_trades = []
    for account_hash in src.account_hashes():
        txns = src.transactions(account_hash, start, end)
        fills = parse_fills(txns, account_hash)
        trades = build_trades(fills)
        for t in trades:
            journal.upsert_trade(t)
        all_trades.extend(trades)
        print(f"account ...{account_hash[-4:]}: {len(fills)} fills -> {len(trades)} trades")

    # Snapshot the CURRENT chain for every traded underlying. Schwab has no
    # historical chains, so this snapshot is only meaningful for trades opened
    # near now — build_context leaves GEX/IV "unknown" otherwise.
    now = datetime.now(timezone.utc)
    for underlying in sorted({t.underlying for t in all_trades}):
        try:
            chain = src.option_chain(underlying)
            if chain:
                journal.save_chain_snapshot(underlying, now, chain.get("underlyingPrice"), chain)
                print(f"chain snapshot saved: {underlying}")
        except Exception as e:  # a non-optionable symbol shouldn't kill the sync
            print(f"chain snapshot skipped for {underlying}: {e}")

    candles = src.price_history(
        config.REGIME_SYMBOL, end - timedelta(days=config.REGIME_LOOKBACK_DAYS), end
    )
    regime = None
    if len(candles) >= 100:
        regime = fit_regime(candles, config.REGIME_STATES)
        print(f"regime fitted on {len(candles)} {config.REGIME_SYMBOL} candles")
    else:
        print(f"not enough {config.REGIME_SYMBOL} history for regime fit ({len(candles)} candles)")

    provider = JournalChainProvider(journal)
    for t in all_trades:
        ctx = build_context(t, regime, candles, provider)
        journal.save_context(t.trade_id, asdict(ctx))
    print(f"synced {len(all_trades)} trades with entry context")


def cmd_thesis(args, journal: Journal):
    if args.pending:
        rows = journal.trades_without_thesis()
        if not rows:
            print("No trades are missing a thesis.")
            return
        print(f"{len(rows)} trade(s) need a thesis:")
        for row in rows:
            _print_trade_line(row)
        return

    if not args.trade_id:
        raise SystemExit("Provide a trade_id (see `thesis --pending`) or use --pending.")
    row = journal.get_trade_row(args.trade_id)
    if row is None:
        raise SystemExit(f"No trade with id {args.trade_id!r} in the journal.")

    text = args.text
    if not text:
        print("Enter your thesis AS IT WAS AT ENTRY (end with a blank line):")
        lines = []
        for line in sys.stdin:
            if line.strip() == "":
                break
            lines.append(line.rstrip("\n"))
        text = "\n".join(lines).strip()
    if not text:
        raise SystemExit("Empty thesis; nothing saved.")
    journal.save_thesis(args.trade_id, text, args.conviction)
    print(f"Thesis saved for {args.trade_id} (conviction {args.conviction}/5).")


def cmd_export(args, journal: Journal):
    if args.trade_id:
        row = journal.get_trade_row(args.trade_id)
        if row is None:
            raise SystemExit(f"No trade with id {args.trade_id!r}.")
        rows = [row]
    else:
        rows = journal.gradeable_trades()
    if not rows:
        print("Nothing to export: no closed trades with a thesis awaiting a grade.")
        return

    qdir = _queue_dir()
    written = []
    for row in rows:
        thesis = journal.get_thesis(row["trade_id"])
        if thesis is None:
            print(f"skipping {row['trade_id']}: no thesis recorded")
            continue
        context = journal.get_context(row["trade_id"]) or {}
        prompt = build_manual_prompt(row, context, thesis)
        path = qdir / f"{row['trade_id']}.txt"
        path.write_text(prompt, encoding="utf-8")
        written.append(path)
        print(f"wrote {path}")

    if written:
        print()
        print("Next steps (free path, no API key):")
        print("  1. Open each .txt above and paste its FULL contents into a new")
        print("     Claude.ai conversation (claude.ai, free account works).")
        print("  2. Claude replies with a single JSON object.")
        print(f"  3. Save each reply as <trade_id>.json in {qdir}/")
        print("  4. Run: python run.py ingest")


def cmd_ingest(args, journal: Journal):
    if args.trade_id:
        row = journal.get_trade_row(args.trade_id)
        if row is None:
            raise SystemExit(f"No trade with id {args.trade_id!r}.")
        print(f"Paste the JSON grade for {args.trade_id}, then EOF (Ctrl-D):")
        grade = _parse_json(sys.stdin.read())
        journal.save_grade(args.trade_id, grade, model="manual/claude.ai")
        print(f"grade stored for {args.trade_id}")
        return

    qdir = _queue_dir()
    stored = 0
    for path in sorted(qdir.glob("*.json")):
        trade_id = path.stem
        if journal.get_trade_row(trade_id) is None:
            print(f"skipping {path.name}: no matching trade in the journal")
            continue
        try:
            grade = _parse_json(path.read_text(encoding="utf-8"))
        except (ValueError, json.JSONDecodeError) as e:
            print(f"skipping {path.name}: could not parse JSON ({e})")
            continue
        journal.save_grade(trade_id, grade, model="manual/claude.ai")
        stored += 1
        print(f"grade stored for {trade_id}")
    print(f"{stored} grade(s) ingested. Run `python run.py patterns` to see the report.")


def cmd_grade(args, journal: Journal):
    config.require("ANTHROPIC_API_KEY")
    grader = Grader()
    if args.trade_id:
        row = journal.get_trade_row(args.trade_id)
        if row is None:
            raise SystemExit(f"No trade with id {args.trade_id!r}.")
        rows = [row]
    else:
        rows = journal.gradeable_trades()
    if not rows:
        print("Nothing to grade: no closed trades with a thesis awaiting a grade.")
        return
    for row in rows:
        thesis = journal.get_thesis(row["trade_id"])
        if thesis is None:
            print(f"skipping {row['trade_id']}: no thesis recorded")
            continue
        context = journal.get_context(row["trade_id"]) or {}
        print(f"grading {row['trade_id']} via {grader.model} ...")
        grade = grader.grade(row, context, thesis)
        journal.save_grade(row["trade_id"], grade, model=grader.model)
        print(f"  -> {grade.get('thesis_grade')} / {grade.get('attribution')}")


def cmd_patterns(args, journal: Journal):
    print(format_report(summarize(journal)))


def cmd_show(args, journal: Journal):
    row = journal.get_trade_row(args.trade_id)
    if row is None:
        raise SystemExit(f"No trade with id {args.trade_id!r}.")
    print("TRADE")
    for k, v in row.items():
        print(f"  {k}: {v}")
    context = journal.get_context(args.trade_id)
    print("\nCONTEXT")
    print(json.dumps(context, indent=2) if context else "  (none)")
    thesis = journal.get_thesis(args.trade_id)
    print("\nTHESIS")
    if thesis:
        print(f"  conviction: {thesis['conviction']}/5 (recorded {thesis['recorded_at']})")
        print("  " + str(thesis["thesis_text"]).replace("\n", "\n  "))
    else:
        print("  (none)")
    grade = journal.get_grade(args.trade_id)
    print("\nGRADE")
    print(json.dumps(grade, indent=2) if grade else "  (none)")


# ---- entry point ------------------------------------------------------------


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="options_copilot",
        description=(
            "Retrospective options-trading decision journal. Grades past decisions "
            "(skill vs. luck); never recommends trades."
        ),
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("sync", help="pull fills from Schwab, build trades + entry context")
    p.add_argument("--days", type=int, default=365, help="lookback window (default 365)")
    p.set_defaults(func=cmd_sync)

    p = sub.add_parser("thesis", help="record the thesis you had at entry")
    p.add_argument("trade_id", nargs="?", help="trade id (see `thesis --pending`)")
    p.add_argument("--conviction", type=int, choices=range(1, 6), default=3, metavar="1-5")
    p.add_argument("--text", help="thesis text (otherwise read from stdin)")
    p.add_argument("--pending", action="store_true", help="list trades missing a thesis")
    p.set_defaults(func=cmd_thesis)

    p = sub.add_parser("export", help="write grading prompts for the free Claude.ai loop")
    p.add_argument("--trade-id")
    p.set_defaults(func=cmd_export)

    p = sub.add_parser("ingest", help="store grade JSON replies saved from Claude.ai")
    p.add_argument("--trade-id", help="read one grade JSON from stdin for this trade")
    p.set_defaults(func=cmd_ingest)

    p = sub.add_parser("grade", help="optional paid path: grade via the Anthropic API")
    p.add_argument("--trade-id")
    p.set_defaults(func=cmd_grade)

    p = sub.add_parser("patterns", help="aggregate pattern report over all grades")
    p.set_defaults(func=cmd_patterns)

    p = sub.add_parser("show", help="print a trade with its context, thesis, and grade")
    p.add_argument("trade_id")
    p.set_defaults(func=cmd_show)

    args = parser.parse_args(argv)
    journal = Journal(config.DB_PATH)
    try:
        args.func(args, journal)
    finally:
        journal.close()
