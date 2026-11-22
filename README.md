# Indian Stock Rebound Scanner

Research scanner for NIFTY 500 and optional other NSE/BSE lists. **Not a live trading platform.** It never places orders. It combines a strict daily lower Bollinger Band condition with support proximity to **150-week/200-week** EMAs, and an optional evidence-based fundamental score. Design date: 2026-11-20.

## What is implemented now

- A manually supplied official index constituent CSV (NIFTY 500 preferred), ISIN deduplication, NSE-first if first row is NSE.
- Two price inputs: manual CSV for compliant/vendor-agnostic use and an **explicit opt-in unofficial Yahoo research adapter**. The latter is not guaranteed, may be unavailable/rate-limited, and has personal-use and licensing restrictions. Do not use it unless you have confirmed the applicable terms permit your use.
- Population-std lower BB(20,1.5); strict `Daily High < Lower BB`; 150/200-week SMA-seeded EMAs, with point-in-time W-FRI alignment and independent 200/250-week observation eligibility thresholds.
- Strict (≤2% candle range-to-EMA), Near (2–5%), Approaching Support (≤5% but without the BB condition).
- Per-ticker error isolation, freshness checks, holiday file, a conservative recent corporate-action guard, global universe coverage checks, price caching for Yahoo mode, custom relative strength percentile and 8-condition Minervini **diagnostic** (not a gate).
- Optional **source-documented** fundamental CSV and conservative 100-point score. Without sourced financial input, a technical candidate is **unverified**, never fundamentally qualified.
- CSV outputs, Google Sheets dashboard integration, append-only state transitions and deduplication in Sheet history, configuration through Settings tab, GitHub Actions schedule.
- Automated offline acceptance tests. Telegram extension reserved for a later phase.

## What is not yet complete

- **There is no guaranteed free, fully licensed daily OHLCV + complete point-in-time Indian fundamentals provider** bundled with this project. Automatic fundamental extraction is not implemented. Full historical fundamental backtesting, historical index membership, verification of all corporate actions, official exchange holiday autodownload, and reliable production-grade operation require additional verified sources/integration.
- User-entered financial values are **source-documented, not independently verified by code**. Do not treat the label “qualified” as proof that the cited documents match a value; validate imports before relying on them.
- The initial weekly-EMA implementation uses completed Friday-labelled weeks. If Friday is an exchange holiday, the EMA conservatively continues using the previous completed week until the next normal Friday.
- Price data are used with a consistent OHLC basis, Yahoo `auto_adjust=False`. Always compare indicator values against a chart using the same historical adjustment and EMA seed convention, which may differ from TradingView defaults.
- Index membership imported today cannot prevent historical survivorship bias. Full retrospective results need time-stamped constituents.
- Scheduled GitHub workflows can start late or be skipped; GitHub provides no precise SLA. Check Actions failures; later add a watchdog.

## 1. Local setup (beginner steps)

1. Download the ZIP, unzip it, and open a terminal inside the `indian-stock-rebound-scanner` directory.
2. Install Python 3.12 and create an environment:
   ```bash
   python -m venv .venv
   # Windows: .venv\Scripts\activate
   # macOS/Linux: source .venv/bin/activate
   pip install -r requirements.txt
   ```
3. Test entirely offline: `pytest -q`.
4. Optional **synthetic smoke test**, safe without real data:
   ```bash
   python scripts_generate_demo.py
   python -m src.cli --source csv --universe data/demo_universe.csv --as-of 2026-11-19 --no-sheets
   ```
   Synthetic outputs are not stock recommendations; remove `data/prices/DEMO*.csv` afterward.
5. Create `data/nifty500.csv` by manually downloading **Download List of Nifty 500 Stocks (.csv)** from https://www.niftyindices.com/indices/equity/broad-based-indices/NIFTY-500 . Review its terms first. Expected headers include `Company Name,Industry,Symbol,Series,ISIN Code`. Do **not** scrape NSE automatically.
6. Option A, CSV data: Set `price_provider: csv` in `config.yaml`. Put real licensed/vendor-permitted daily price CSVs in `data/prices/SYMBOL.NS.csv` with `Date,Open,High,Low,Close,Volume`.
7. Option B, optional research adapter: In `config.yaml`, use `price_provider: yahoo_research` and `allow_yahoo_research: true` only after verifying you have permission under Yahoo's terms. Historical downloads can be rate-limited or blocked. Manual CSV remains the fallback. Run `python -m src.cli --config config.yaml --no-sheets` (only after close on an exchange trading day) or use `--as-of YYYY-MM-DD` for an earlier valid trading session.
8. Inspect `output/all_stocks.csv`, `output/technical_unverified.csv`, `output/near_matches.csv`, `output/strict_qualified.csv`, `output/technical_below_score.csv`, `output/errors.csv`, and `output/run_summary.json`. If >15% of universe has no fresh valid daily data, the scan fails and refuses to publish results. Recently listed securities with inadequate EMA history are marked `INSUFFICIENT_HISTORY` rather than being mistaken for download failures.

## 2. Optional verified financial inputs

`data/fundamentals.csv` is intentionally empty. It is a **long table**, one documented metric per company per line:

```csv
ticker,metric,value,period_end,published_at,source_url
RELIANCE.NS,eps_q_yoy_pct,35,2026-09-30,2026-10-25,https://EXAMPLE-REAL-SOURCE-URL
```

**This one example row is an illustration only. The number and URL are fictional; do not paste it as actual market data.**

Valid metric keys:
`eps_q_yoy_pct,eps_annual_yoy_pct,eps_accel_pp,revenue_q_yoy_pct,revenue_annual_yoy_pct,roe_pct,roce_pct,roa_pct,operating_cash_flow_positive,capital_adequacy_pct`.

Use `operating_cash_flow_positive = 1` only when verified positive and `0` for verified negative. For `eps_accel_pp` use latest quarterly EPS growth (YoY percent) minus previous quarter's YoY EPS growth. Each metric needs its own publication date and source URL, which is filtered against the scan date. No value is generated when metadata is missing or stale (quarterly >210 days; annual >570 days after publication).

Conservative fundamental rule: essentials `eps_q_yoy_pct`, `revenue_q_yoy_pct`, `roe_pct`, and `roce_pct` (nonfinancial) or `roa_pct` (financials); ≥70 points of documented coverage; ≥70/100 scored. Banks/NBFC use special ROA/capital adequacy weights. **Insurance companies are withheld from qualification until a specialized scoring rubric is introduced.** Some banks' concept of revenue differs from industrial companies; review before applying common thresholds.

Relative strength ranks are *custom 0–100 percentiles* of weighted 3/6/12-month returns: `0.4 × 3m + 0.3 × 6m + 0.3 × 12m`. They are not the proprietary IBD RS rating. Industries require at least 5 eligible peers to calculate industry leadership ranks.

## 3. Google Sheets dashboard

1. Make a new blank Google Sheet in your Google account and copy its spreadsheet ID (the text between `/d/` and `/edit` in its URL).
2. In Google Cloud Console, create a project; enable **Google Sheets API**; create a service account and its JSON key if allowed by your Google Cloud organization and security policy. Keep the key private. This ordinarily does not require a billing subscription for ordinary free-tier API use, but check quota/billing settings.
3. Share only that spreadsheet with the service account's `client_email` as **Editor**. Do not grant Drive-wide access.
4. Locally set environment variables: `GOOGLE_SHEET_ID=...` and `GOOGLE_SERVICE_ACCOUNT_JSON='{"type":"service_account", ...}'` (the **complete** JSON). Set `SHEETS_ENABLED=true` or change `sheets_enabled: true` in `config.yaml`. Do not paste credentials into source files.
5. Run the scanner. It creates these tabs if absent: `Dashboard`, `Strict Matches`, `Technical Matches - Unverified`, `Near Matches`, `Technical Matches - Below Score`, `Watchlist`, `Fundamental Analysis`, `Signal History`, `Settings`, `Scan Logs`.
6. The `Settings` tab holds configurable proximity/score thresholds. After the first run it can be edited; `Signal History` is append-only and survives subsequent refreshes. **Do not manually edit the history headers.**
7. Read and write operations use the service account's minimum Sheets permission. If you want to keep results private, keep the Sheet private and the GitHub repo private.

## 4. GitHub Actions daily automation

1. The deployed workflow downloads the published Nifty constituent CSV into its runner. It accepts an existing `data/nifty500.csv` as an override. Each run records the exact TejHQ dataset revision used in `output/market_source.json`. Keep user-supplied licensed inputs out of public commits.
2. Scheduled runs use the CSV scanner with TejHQ adjusted NSE end-of-day history sourced from exchange bhavcopies. The same cumulative factor is applied to open, high, low and close. Source history begins in 2015. Missing instruments appear in `errors.csv`. The Yahoo adapter remains available for explicitly enabled local research.
3. For Google Sheets publishing, enable the Sheets API in your Google Cloud project, grant the service account Editor access to the destination Sheet, and set repository Actions secrets `GOOGLE_SHEET_ID` and `GOOGLE_SERVICE_ACCOUNT_JSON`. Until the service-account credential is supplied, GitHub emits CSV results and the Sheet can be updated separately.
4. Open **Actions → Daily India Stock Rebound Scan → Run workflow** and select a trading date (YYYY-MM-DD), or leave the field blank to use the current date in India. Use a recent session. This workflow uses current index membership and current corporate-action adjustments, so it is unsuitable for point-in-time backtests.
5. The daily workflow runs weekdays at **22:15 Asia/Kolkata** to give the end-of-day source time to publish. An unavailable or stale session fails the coverage checks.
6. The workflow keeps its CSV results as a GitHub Actions artifact for 14 days. Google Sheets provides cross-run signal history when configured. Artifact-only runs use a fresh checkout and do not preserve signal history across runs.
7. If data is stale, the official holiday file is missing a holiday, or fewer than 85% of stocks have fresh prices, **the job fails closed rather than silently publishing misleading empty results**. Full-universe failures are recorded to `Scan Logs` (when Sheets is connected) without clearing the last good dashboard. Calendar file `data/holidays.csv` accepts `date,name` records and should be manually reviewed annually.
8. GitHub Free private Actions usage has plan-dependent included allowance; monitor Actions minutes and artifact storage to avoid charges. The scan duration depends on provider throttling, data volume and rate limits.

## 5. Signal logic and confirmation

Strict technical setup: `Daily High < BB_Lower` **AND** minimum candle-range-to-either-WEMA distance `≤2%`.
Near: strict lower BB condition but `2% < distance ≤5%`.
Approaching Support: `distance ≤5%` but strict lower BB condition fails.

Fundamental Qualification: strict technical + source-documented essential fields + ≥70 covered scoring points + ≥70 earned points.

Transitions tracked in `Signal History`: initial setup (qualified, unverified, or documented-but-below-score), developing (close above previous daily close), confirmed (close above previous daily high or lower band **and** above previous daily close), invalidated (close >5% below original EMA reference). These are **initial configurable research definitions, not validated trading rules**. Preserve original support EMA and setup high for future-day evaluation.

## 6. Known implementation boundaries and safety

- Yahoo batch download for hundreds of symbols may be blocked or interrupted; provider reliability cannot be promised. Cache can reduce repeat full downloads, but is not a licensed production feed.
- 85% fresh-price coverage threshold is a protection, not evidence that surviving matches are correct. Inspect ticker errors.
- The numerical price basis must be consistent. Yahoo `auto_adjust=False` is used for trading OHLC and weekly EMA. Comparison platforms may use different splits, adjustments, EMA initialization, holiday grouping or sessions. Verify several reference stocks manually before trusting signals.
- Skipping outlier bars or invalid OHLCs can create false weekly closes in corrupted datasets; the tool fails on major recent discontinuities, but cannot correct every provider issue automatically.
- Historical earnings data must be published before the historical scan date; `published_at` is mandatory, not inferred.
- No automatic historical performance claims are generated. Full backtesting is a later phase that must incorporate point-in-time financials, historical constituents, benchmark, costs, slippage, and survivorship limitations.
- Research only. Neither the code nor this document is a recommendation to buy, sell, or hold any stock.

## Companion guides

- `docs/QUICK_START.txt`: concise initial installation steps.
- `docs/TECHNICAL_SPEC.md`: exact indicator, scoring, and state definitions.
- `docs/dashboard_template.xlsx`: optional Google Sheets-compatible starter workbook. Upload the `.xlsx` to Google Drive and open as a native Sheet, or create a blank native Sheet and let the exporter create tabs automatically.
