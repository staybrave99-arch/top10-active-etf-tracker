"""Core computations for the active-ETF accumulation indicator.

Implements the design in 主動式ETF持股加碼指標_設計文件.md (§4-§9, §12) with
the parameters confirmed for this project:

    q_eps           = 0                (股數變化門檻)
    w_eps           = 0.01             (權重變化門檻, pp)
    lambda_         = 0.25             (連續天數持續性加成強度)
    streak_mode     = "WEIGHT_ONLY"    (連續判定只看權重方向, 不強制股數同向)
    agg_mode        = "AUM"            (跨ETF合併: 依基金規模加權)
    score_form      = "MULT"           (cum_dw * (1+lambda*(streak-1)))
    ma_window       = 20               (BIAS 均線天數)
    trend_mode      = "TAG"            (象限只標籤, 不改排序)

streak_mode=WEIGHT_ONLY note: this drops the doc's harder "Δq>0 AND Δw>0"
buy condition down to "Δw>0" alone, so a pure price-driven weight increase
(no share purchase) can register as a buy day. That's the tradeoff the
simpler mode accepts; it was chosen over STRICT for this project.
"""

import pandas as pd

Q_EPS = 0.0
W_EPS = 0.01
LAMBDA = 0.25
MA_WINDOW = 20


def compute_flags_and_streaks(holdings: pd.DataFrame, streak_mode: str = "WEIGHT_ONLY") -> pd.DataFrame:
    """Per (etf_code, stock_code) time series: add day-over-day deltas,
    buy/sell flags, streak lengths, cumulative streak weight-change, and
    the MULT-form accumulation score (a_up / a_dn).

    Required input columns: etf_code, stock_code, trade_date, shares, weight

    streak_mode:
      "WEIGHT_ONLY" (this project's default) - a day counts as a buy purely
      on Δweight > w_eps, regardless of share direction.
      "STRICT" (the design doc's original default) - requires both
      Δshares > q_eps AND Δweight > w_eps, so a weight increase driven
      purely by price movement (no actual share purchase) doesn't count.
    """

    def _per_group(g):
        etf_code, stock_code = g.name
        g = g.sort_values("trade_date").reset_index(drop=True)
        g["etf_code"] = etf_code
        g["stock_code"] = stock_code
        # First appearance of a (etf, stock) pair has no prior day to diff
        # against, so -- matching the design doc's worked example -- it's
        # a baseline observation only, never itself a flagged buy/sell day.
        prev_shares = g["shares"].shift(1)
        prev_weight = g["weight"].shift(1)
        g["d_shares"] = g["shares"] - prev_shares
        g["d_weight"] = g["weight"] - prev_weight
        is_first = prev_shares.isna()

        if streak_mode == "STRICT":
            g["buy_flag"] = ((g["d_shares"] > Q_EPS) & (g["d_weight"] > W_EPS)).astype(int)
            g["sell_flag"] = ((g["d_shares"] < -Q_EPS) & (g["d_weight"] < -W_EPS)).astype(int)
        else:
            g["buy_flag"] = (g["d_weight"] > W_EPS).astype(int)
            g["sell_flag"] = (g["d_weight"] < -W_EPS).astype(int)
        g.loc[is_first, ["buy_flag", "sell_flag"]] = 0
        g["d_shares"] = g["d_shares"].fillna(0)
        g["d_weight"] = g["d_weight"].fillna(0)

        buy_streak, sell_streak, cum_up, cum_dn = [], [], [], []
        bs = ss = 0
        cu = cd = 0.0
        for buy, sell, dw in zip(g["buy_flag"], g["sell_flag"], g["d_weight"]):
            bs = bs + 1 if buy else 0
            cu = cu + dw if buy else 0.0
            ss = ss + 1 if sell else 0
            cd = cd + (-dw) if sell else 0.0
            buy_streak.append(bs)
            sell_streak.append(ss)
            cum_up.append(cu)
            cum_dn.append(cd)

        g["buy_streak"] = buy_streak
        g["sell_streak"] = sell_streak
        g["cum_dw_up"] = cum_up
        g["cum_dw_dn"] = cum_dn

        g["a_up"] = g["cum_dw_up"] * (1 + LAMBDA * (g["buy_streak"] - 1).clip(lower=0))
        g["a_dn"] = g["cum_dw_dn"] * (1 + LAMBDA * (g["sell_streak"] - 1).clip(lower=0))
        g.loc[g["buy_flag"] == 0, "a_up"] = 0.0
        g.loc[g["sell_flag"] == 0, "a_dn"] = 0.0
        return g

    return (
        holdings.sort_values(["etf_code", "stock_code", "trade_date"])
        .groupby(["etf_code", "stock_code"], group_keys=False)
        .apply(_per_group)
    )


def aggregate_cross_etf(scored: pd.DataFrame, etf_aum: pd.DataFrame) -> pd.DataFrame:
    """Combine per-ETF scores into one signal per stock per day, weighted
    by each ETF's AUM (in NTD 億元, i.e. net_asset / 1e8) so bigger funds
    carry proportionally more influence.

    etf_aum columns: etf_code, trade_date, aum (net_asset in NTD)
    """
    df = scored.merge(etf_aum, on=["etf_code", "trade_date"], how="left")
    df["aum_weight"] = df["aum"] / 1e8
    df["a_up_w"] = df["a_up"] * df["aum_weight"]
    df["a_dn_w"] = df["a_dn"] * df["aum_weight"]

    agg = (
        df.groupby(["trade_date", "stock_code"])
        .agg(
            buy_pressure=("a_up_w", "sum"),
            sell_pressure=("a_dn_w", "sum"),
            n_buy=("buy_flag", "sum"),
            n_sell=("sell_flag", "sum"),
            max_buy_streak=("buy_streak", "max"),
            max_sell_streak=("sell_streak", "max"),
            tot_d_shares=("d_shares", "sum"),
        )
        .reset_index()
    )
    agg["net_score"] = agg["buy_pressure"] - agg["sell_pressure"]
    return agg


def compute_bias(prices: pd.DataFrame, window: int = MA_WINDOW) -> pd.DataFrame:
    """Add an N-day moving average and the %-deviation (BIAS) from it.

    prices columns: stock_code, trade_date, close
    """
    df = prices.sort_values(["stock_code", "trade_date"]).copy()
    df["ma"] = df.groupby("stock_code")["close"].transform(
        lambda s: s.rolling(window, min_periods=window).mean()
    )
    df["bias"] = (df["close"] - df["ma"]) / df["ma"] * 100
    return df


def classify_quadrant(net_score, bias):
    if pd.isna(net_score) or pd.isna(bias):
        return None
    if net_score >= 0 and bias >= 0:
        return "順勢加碼"
    if net_score >= 0 and bias < 0:
        return "逆勢加碼"
    if net_score < 0 and bias >= 0:
        return "強勢減碼"
    return "弱勢減碼"


def build_report(
    holdings: pd.DataFrame,
    etf_aum: pd.DataFrame,
    prices: pd.DataFrame,
    streak_mode: str = "WEIGHT_ONLY",
) -> pd.DataFrame:
    """End-to-end: holdings + AUM + prices -> one row per (trade_date, stock_code)
    with net_score, bias, quadrant tag, and breadth stats.
    """
    scored = compute_flags_and_streaks(holdings, streak_mode=streak_mode)
    agg = aggregate_cross_etf(scored, etf_aum)
    bias_df = compute_bias(prices)[["stock_code", "trade_date", "close", "ma", "bias"]]
    merged = agg.merge(bias_df, on=["trade_date", "stock_code"], how="left")
    merged["quadrant"] = merged.apply(lambda r: classify_quadrant(r["net_score"], r["bias"]), axis=1)
    return merged
