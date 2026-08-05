# Setup checklist

## 1. Python environment

```bash
cd options_copilot
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 2. Schwab developer app

1. Sign up at https://developer.schwab.com and create an "Individual Developer" app.
2. Add **both** API products to the app:
   - **Accounts and Trading Production**
   - **Market Data Production**
3. Set the callback URL to exactly `https://127.0.0.1:8182` (must match
   `SCHWAB_CALLBACK_URL` in `.env` character-for-character).
4. Wait until the app status shows **"Ready For Use"** (approval can take a
   few days; "Approved - Pending" will not work).

## 3. Configure secrets

```bash
cp .env.example .env
```

Edit `.env` and fill in `SCHWAB_APP_KEY` and `SCHWAB_APP_SECRET`.
Leave `ANTHROPIC_API_KEY` empty unless you want the optional paid grading path.

## 4. First sync

```bash
python run.py sync --days 365
```

- The first run opens your browser for the Schwab OAuth login and runs a
  temporary local HTTPS server on port 8182 to catch the redirect. Your
  browser will warn about the self-signed localhost certificate — proceed.
- The token is saved to `data/schwab_token.json` and refreshes automatically.
- The refresh token expires after **7 days idle**; when that happens the next
  sync re-opens the browser login. This is Schwab policy — just log in again.

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

Everything lives under `data/` (SQLite journal, token, chain snapshots,
grading queue) and is gitignored along with `.env`.
