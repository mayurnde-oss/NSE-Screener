# Calculation and data contract

## 1. Daily lower Bollinger Band

On the latest **completed NSE/BSE daily candle**, compute the 20-close simple moving average and the 20-close population standard deviation (`ddof=0`). The lower band is `SMA20(close) - 1.5 * stdev_population20(close)`. The strict candle test uses **daily High < lower band**. Equality fails. The band includes that day's completed Close.

## 2. Weekly support

Resample the *same OHLC price basis* to `W-FRI` completed weekly Close observations. A 150-week EMA uses `alpha=2/151`; a 200-week EMA uses `alpha=2/201`. Seed the first EMA at week n-1 using SMA of the first n weekly closes, then update recursively with `EMA_n(t) = alpha*Close(t) + (1-alpha)*EMA_n(t-1)`.

For Monday–Thursday daily observations use the previous Friday's EMA; on Friday after completed close the Friday-labeled EMA can be used. Friday holidays conservatively retain previous completed Friday until a later completed weekly label. This rule avoids falsely using later-in-week closing prices in a Monday/Thursday scan.

For reliability, the 150-week support is evaluated only with ≥200 weekly observations, and the 200-week support with ≥250. Stocks with shorter history are marked insufficient, **not** rejected as if price data had failed to download. A stock with 225 weeks can qualify via 150-week EMA without requiring its 200-week EMA.

## 3. Range to support

Given daily low L, high H, and weekly EMA E:

```
DistancePct = 100 * max(L-E, E-H, 0) / E
```

This formula returns 0 if the daily candle overlaps or touches E. Compute independently for E150 and E200, take the minimum valid distance, and label the nearest support. The strict setup is BB condition AND min distance ≤2%; near setup is BB condition AND `2 < min distance ≤5`; approaching is min distance ≤5 without the strict BB condition.

## 4. Price basis and data hygiene

The optional Yahoo adapter currently requests `auto_adjust=False`, using one consistent vendor-provided OHLC basis for both daily and weekly candles. This prevents mixing adjusted close with non-adjusted high/low. It is not a guarantee of split or dividend consistency: verify vendor data and charting settings. The adapter uses *unofficial* research access, has no SLA, and is opt-in only. The CSV adapter accepts user-supplied lawful daily OHLCV records.

The scanner rejects a missing latest trading bar, invalid OHLC/volume rows, a recent >55% single-day close discontinuity requiring corporate-action review, and a run when >15% of the universe lacks usable fresh daily data. Missing calendar holidays must be supplied manually in `data/holidays.csv`, because automated exchange-calendar collection is not included.

## 5. Custom RS and Minervini-inspired diagnostics

Custom momentum factor = `0.4 * (close / close[-63] - 1) + 0.3 * (close / close[-126] - 1) + 0.3 * (close / close[-252] - 1)`. Percentile-rank it against the whole successfully fetched universe. The top name receives 100, regardless of its absolute return. Industry percentiles need ≥5 valid names per matching industry label. The relative-strength percentile is **not** an official IBD RS rating.

Trend-template count is 0–8 from seven daily-price trend checks plus a custom RS percentile ≥70. It is diagnostic only, not a hard qualification gate, because deep weekly EMA corrections can violate it.

## 6. Fundamental evidence and score

No fundamental data is invented or scraped. Each input value in `data/fundamentals.csv` includes a metric identifier, numeric value, fiscal `period_end`, real `published_at`, and source HTTPS URL. Values become eligible only after the stated publication date and within a bounded age window. Input existence alone is **not** independent verification of the source document's numbers.

Weights total 100: earnings 25; revenue 20; ROE/ROCE or banking ROE/ROA 20; full-universe custom RS percentile 20; positive cash flow/capital adequacy and sector leadership percentile 15. Weights for categorical metrics count if the threshold is met; RS weights are continuous 0–20, sector leadership 0–7.

The user must supply sufficient source-documented metrics to cover ≥70 weighted points, including four essential metrics, before the engine can call fundamental data sufficiently documented. To enter `Strict Matches`, the documented candidate's score must also be ≥70. Documented scores below 70 are not mislabelled “unverified” and appear in a separate tab. Insurers are not automatically eligible for banking ROA rubrics.

## 7. State transitions and caveat

Strict setup is not confirmation. The initial separate confirmation research rule is: a previous setup exists, latest Close > previous completed daily Close AND (`Close > previous completed daily High` OR `Close > current lower daily BB`). Developing means a previous setup exists, Close > prior daily Close but no confirmation. Invalidated means Close < original chosen EMA support ×0.95. A state-change entry is appended only when new; repeated scans do not duplicate events. These thresholds are hypotheses for research and need validation.

Historical financial screening requires point-in-time quarterly statements and historical index constituents. Backtesting a current NIFTY500 snapshot alone has survivorship bias; it must not be used to claim proven profitability.
