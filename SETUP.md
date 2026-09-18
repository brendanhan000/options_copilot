# Setup checklist

## 1. Python environment

```bash
cd options_copilot
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 2. Schwab access (central hub)

Schwab access is handled by the central **schwab_hub** (`../schwab_hub`), which owns the
Schwab app credentials and the login. See `../schwab_hub/README.md` for the one-time setup
(the app needs both **Accounts and Trading Production** and **Market Data Production**).

```bash
# the schwab_hub client is already installed by `pip install -r requirements.txt` above
../schwab_hub/run.sh login    # first time, then weekly (Schwab refresh tokens last 7 days)
../schwab_hub/run.sh          # leave running
```

## 3. Configure secrets

```bash
cp .env.example .env
```

Leave `ANTHROPIC_API_KEY` empty unless you want the optional paid grading path.

## 4. First sync

```bash
python run.py sync --days 365
```

The sync talks to the hub, so it needs no browser login or Schwab secrets of its own.

## 5. Record theses, then grade (free loop, default)

```bash
python run.py thesis --pending            # list trades needing a thesis
python run.py thesis <trade_id> --conviction 4   # then type your thesis, blank line to end
python run.py export                      # writes data/grading_queue/<trade_id>.txt
```

For each `.txt` file: paste its full contents into a new Claude.ai
conversation. Claude replies with a single JSON object. Save each reply as
`<trade_id>.json` in `data/grading_queue/`, then:

```bash
python run.py ingest
python run.py patterns
```

## Optional: paid API grading

Set `ANTHROPIC_API_KEY` in `.env`, then replace the export/ingest steps with:

```bash
python run.py grade
```

## Handy extras

```bash
python run.py show <trade_id>    # trade + context + thesis + grade in one view
```

Everything lives under `data/` (SQLite journal, chain snapshots,
grading queue) and is gitignored along with `.env`.
