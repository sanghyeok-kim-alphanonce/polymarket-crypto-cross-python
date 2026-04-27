"""Render a 4-hour PnL dashboard from trade-infra settlement results.

Outputs an interactive HTML to trade_archive/trad_alph/pnl_4h_dashboard.html
"""
from __future__ import annotations

import glob
import json
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

ROOT = Path(__file__).resolve().parents[1]
SETTLE_DIR = ROOT / "trade_archive/trad_alph/trade_infra_data/settlement"
EXEC_DIR = ROOT / "trade_archive/trad_alph/trade_infra_data/strategies/cross_5m_improve_btc/execution"
OUT_HTML = ROOT / "trade_archive/trad_alph/pnl_4h_dashboard.html"
BUCKET = "4h"


def load_settlements() -> pd.DataFrame:
    rows = []
    for path in sorted(glob.glob(str(SETTLE_DIR / "results-*.jsonl"))):
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
    df = pd.DataFrame(rows)
    df["ts"] = pd.to_datetime(df["ts"], utc=True, format="ISO8601")
    df["slug_kind"] = df["slug"].str.rsplit("-", n=1).str[0]
    df["cost"] = df["size"] * df["avg_price"]
    df["win"] = df["pnl"] > 0
    return df.sort_values("ts").reset_index(drop=True)


def load_exec_counts() -> pd.DataFrame:
    rows = []
    for path in sorted(glob.glob(str(EXEC_DIR / "events-*.jsonl"))):
        date = path.split("events-")[-1].replace(".jsonl", "")
        counts = {"Filled": 0, "Rejected": 0, "Cancelled": 0, "Other": 0}
        with open(path) as f:
            for line in f:
                if not line.strip():
                    continue
                t = json.loads(line).get("type", "Other")
                counts[t] = counts.get(t, 0) + 1
        rows.append({"date": pd.Timestamp(date, tz="UTC"), **counts})
    return pd.DataFrame(rows)


def build_buckets(df: pd.DataFrame, freq: str) -> pd.DataFrame:
    g = (
        df.set_index("ts")
        .resample(freq, origin="epoch", label="left", closed="left")
        .agg(
            pnl=("pnl", "sum"),
            n=("pnl", "size"),
            wins=("win", "sum"),
            size=("size", "sum"),
            cost=("cost", "sum"),
            payout=("payout", "sum"),
            avg_price=("avg_price", "mean"),
        )
    )
    g["win_rate"] = (g["wins"] / g["n"]).fillna(0)
    g["cum_pnl"] = g["pnl"].cumsum()
    g["wavg_price"] = (g["cost"] / g["size"]).where(g["size"] > 0)
    g["color"] = g["pnl"].apply(lambda x: "#16a34a" if x > 0 else ("#dc2626" if x < 0 else "#9ca3af"))
    return g.reset_index()


def render(
    df: pd.DataFrame,
    h4: pd.DataFrame,
    daily: pd.DataFrame,
    execs: pd.DataFrame,
) -> None:
    total = df["pnl"].sum()
    fig = make_subplots(
        rows=5,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.045,
        row_heights=[0.30, 0.20, 0.18, 0.16, 0.16],
        subplot_titles=(
            f"4h PnL (bars) + cumulative PnL (line)  —  {len(df)} settled trades, gross PnL ${total:.2f}  "
            f"<span style='color:#dc2626'>⚠ fee NOT subtracted (settler/main.rs:230 → pnl = payout − fill_cost only; Polygon gas / maker-taker fee missing)</span>",
            "Daily PnL (cleaner trend; same PnL summed by UTC day)",
            "Trade count per 4h bucket (color = win rate)",
            "Size-weighted avg fill price per bucket (size = marker size; high price = paying more for entry, lower edge if you win)",
            "Trader execution events per day (Filled vs Rejected — Apr 22+ shows 0 fills locally but settlements still arrive ⇒ another agent on same wallet?)",
        ),
    )

    customdata = h4[["n", "wins", "win_rate", "size", "cost", "payout", "avg_price"]].values
    hover = (
        "<b>%{x|%Y-%m-%d %H:%M UTC}</b><br>"
        "PnL (gross): $%{y:.2f}<br>"
        "Trades: %{customdata[0]} (wins %{customdata[1]}, %{customdata[2]:.1%})<br>"
        "Notional shares: %{customdata[3]:.2f}<br>"
        "Fill cost: $%{customdata[4]:.2f}  →  payout $%{customdata[5]:.2f}<br>"
        "Avg fill price: %{customdata[6]:.3f}<extra></extra>"
    )

    fig.add_trace(
        go.Bar(x=h4["ts"], y=h4["pnl"], marker_color=h4["color"], name="4h PnL",
               customdata=customdata, hovertemplate=hover),
        row=1, col=1,
    )
    fig.add_trace(
        go.Scatter(x=h4["ts"], y=h4["cum_pnl"], mode="lines",
                   line=dict(color="#1f2937", width=2), name="Cumulative PnL",
                   yaxis="y2",
                   hovertemplate="<b>%{x|%Y-%m-%d %H:%M}</b><br>Cum PnL: $%{y:.2f}<extra></extra>"),
        row=1, col=1,
    )

    daily_hover = (
        "<b>%{x|%Y-%m-%d}</b><br>"
        "PnL: $%{y:.2f}<br>"
        "Trades: %{customdata[0]} (win rate %{customdata[1]:.1%})<br>"
        "Cost $%{customdata[2]:.2f} → payout $%{customdata[3]:.2f}<extra></extra>"
    )
    fig.add_trace(
        go.Bar(
            x=daily["ts"], y=daily["pnl"], marker_color=daily["color"], name="Daily PnL",
            customdata=daily[["n", "win_rate", "cost", "payout"]].values,
            hovertemplate=daily_hover,
        ),
        row=2, col=1,
    )

    fig.add_trace(
        go.Bar(
            x=h4["ts"], y=h4["n"],
            marker=dict(color=h4["win_rate"], colorscale="RdYlGn", cmin=0, cmax=1,
                        colorbar=dict(title="Win rate", y=0.55, len=0.18)),
            name="Trades",
            customdata=h4[["wins", "win_rate", "pnl", "size", "wavg_price"]].values,
            hovertemplate=("<b>%{x|%Y-%m-%d %H:%M}</b><br>Trades: %{y}<br>"
                           "Wins: %{customdata[0]} (%{customdata[1]:.1%})<br>"
                           "PnL: $%{customdata[2]:.2f}<br>"
                           "Notional: %{customdata[3]:.2f}<br>"
                           "Avg fill price: %{customdata[4]:.3f}<extra></extra>"),
        ),
        row=3, col=1,
    )

    price_mask = h4["wavg_price"].notna()
    hp = h4[price_mask]
    fig.add_trace(
        go.Scatter(
            x=hp["ts"], y=hp["wavg_price"],
            mode="markers+lines",
            line=dict(color="#94a3b8", width=1),
            marker=dict(
                size=(hp["size"] / hp["size"].max() * 28 + 4).clip(lower=4),
                color=hp["pnl"],
                colorscale="RdYlGn",
                cmid=0,
                line=dict(color="#1f2937", width=0.5),
                colorbar=dict(title="PnL ($)", y=0.31, len=0.18),
            ),
            name="Weighted avg fill price",
            customdata=hp[["pnl", "n", "size", "win_rate"]].values,
            hovertemplate=("<b>%{x|%Y-%m-%d %H:%M}</b><br>"
                           "Avg fill price: %{y:.3f}<br>"
                           "PnL: $%{customdata[0]:.2f}<br>"
                           "Trades: %{customdata[1]} (win rate %{customdata[3]:.1%})<br>"
                           "Notional: %{customdata[2]:.2f}<extra></extra>"),
        ),
        row=4, col=1,
    )

    if not execs.empty:
        fig.add_trace(
            go.Bar(x=execs["date"], y=execs["Filled"], name="Filled (local trader)",
                   marker_color="#16a34a",
                   hovertemplate="<b>%{x|%Y-%m-%d}</b><br>Filled: %{y}<extra></extra>"),
            row=5, col=1,
        )
        fig.add_trace(
            go.Bar(x=execs["date"], y=execs["Rejected"], name="Rejected",
                   marker_color="#dc2626",
                   hovertemplate="<b>%{x|%Y-%m-%d}</b><br>Rejected: %{y}<extra></extra>"),
            row=5, col=1,
        )

    fig.update_layout(
        title=f"trad_alph settlement PnL — {df['ts'].min():%Y-%m-%d} → {df['ts'].max():%Y-%m-%d}  (peak cum +$470 on 04-20, now +$278 → drawdown −$193 in 7 days)",
        height=1280,
        bargap=0.05,
        showlegend=True,
        legend=dict(orientation="h", y=-0.05),
        hovermode="closest",
        barmode="stack",
        yaxis2=dict(overlaying="y", side="right", title="Cumulative PnL ($)", showgrid=False),
        margin=dict(l=60, r=60, t=110, b=60),
    )
    fig.update_yaxes(title_text="4h PnL ($)", row=1, col=1, zeroline=True, zerolinecolor="#9ca3af")
    fig.update_yaxes(title_text="Daily PnL ($)", row=2, col=1, zeroline=True, zerolinecolor="#9ca3af")
    fig.update_yaxes(title_text="Trades / 4h", row=3, col=1)
    fig.update_yaxes(title_text="Avg fill price", row=4, col=1, range=[0.3, 1.0])
    fig.update_yaxes(title_text="Events / day", row=5, col=1)

    OUT_HTML.parent.mkdir(parents=True, exist_ok=True)
    fig.write_html(OUT_HTML, include_plotlyjs="cdn", full_html=True)
    print(f"wrote {OUT_HTML}")


def print_summary(df: pd.DataFrame, buckets: pd.DataFrame) -> None:
    win_buckets = buckets[buckets["pnl"] > 0]
    loss_buckets = buckets[buckets["pnl"] < 0]
    print("\n=== top 10 winning 4h buckets ===")
    print(buckets.nlargest(10, "pnl")[["ts", "pnl", "n", "win_rate", "size"]].to_string(index=False))
    print("\n=== top 10 losing 4h buckets ===")
    print(buckets.nsmallest(10, "pnl")[["ts", "pnl", "n", "win_rate", "size"]].to_string(index=False))
    print(
        f"\nbuckets: total={len(buckets)}  winning={len(win_buckets)}  losing={len(loss_buckets)}  flat={len(buckets) - len(win_buckets) - len(loss_buckets)}"
    )
    print(f"sum_pnl: win=${win_buckets['pnl'].sum():.2f}  loss=${loss_buckets['pnl'].sum():.2f}")


if __name__ == "__main__":
    df = load_settlements()
    h4 = build_buckets(df, BUCKET)
    daily = build_buckets(df, "1D")
    execs = load_exec_counts()
    render(df, h4, daily, execs)
    print_summary(df, h4)
