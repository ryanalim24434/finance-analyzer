"""
Personal Finance Analyzer - an MCP server.

Exposes a transactions CSV as a set of analysis tools any MCP client
(Claude Desktop, Claude Code, MCP Inspector) can discover and call.

Run directly:      python server.py
Inspect locally:   mcp dev server.py

Config:
    TRANSACTIONS_CSV   path to the CSV. Defaults to ./transactions.csv

Expected CSV schema:
    date, merchant, category, amount
    amount > 0 is money out, amount < 0 is money in.
"""

import os
import sys
from pathlib import Path

import pandas as pd
from mcp.server.mcpserver import MCPServer

mcp = MCPServer("finance-analyzer", instructions=(
    "Analyze a personal transactions CSV. Call dataset_overview first to learn "
    "the available date range and categories before using the other tools."
))

CSV_PATH = Path(os.environ.get("TRANSACTIONS_CSV", "transactions.csv")).expanduser()

# stdio transport carries JSON-RPC on stdout, so anything we print for humans
# MUST go to stderr or it corrupts the protocol and the server dies silently.
def log(msg: str) -> None:
    print(msg, file=sys.stderr)


def load() -> pd.DataFrame:
    """Read and normalize the CSV. Raises a clear error the model can relay."""
    if not CSV_PATH.exists():
        raise FileNotFoundError(
            f"No transactions file at {CSV_PATH}. Set TRANSACTIONS_CSV to its "
            f"full path, or run generate_sample_data.py to create one."
        )
    df = pd.read_csv(CSV_PATH)
    missing = {"date", "merchant", "category", "amount"} - set(df.columns)
    if missing:
        raise ValueError(f"CSV is missing required column(s): {sorted(missing)}")
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df["amount"] = pd.to_numeric(df["amount"], errors="coerce")
    bad = df["date"].isna() | df["amount"].isna()
    if bad.any():
        log(f"Dropping {int(bad.sum())} rows with unparseable date or amount")
        df = df[~bad]
    df["month"] = df["date"].dt.to_period("M").astype(str)
    return df


def spend_only(df: pd.DataFrame) -> pd.DataFrame:
    """Expenses only. Income and refunds are negative amounts."""
    return df[df["amount"] > 0]


def money(x: float) -> str:
    return f"${x:,.2f}"


# ---------------------------------------------------------------------------
# Tools. Each docstring becomes the description the model reads when deciding
# whether the tool applies, so they are written for that audience.
# ---------------------------------------------------------------------------

@mcp.tool()
def dataset_overview() -> str:
    """Describe what transaction data is available: date range, row count,
    categories present, and totals. Call this first when you don't yet know
    the shape of the data or which categories and months are valid inputs."""
    df = load()
    spend = spend_only(df)
    income = -df[df["amount"] < 0]["amount"].sum()
    lines = [
        f"Source: {CSV_PATH}",
        f"Rows: {len(df):,}",
        f"Date range: {df['date'].min():%Y-%m-%d} to {df['date'].max():%Y-%m-%d}",
        f"Months covered: {df['month'].nunique()}",
        f"Total spending: {money(spend['amount'].sum())}",
        f"Total income: {money(income)}",
        f"Net: {money(income - spend['amount'].sum())}",
        "",
        "Categories: " + ", ".join(sorted(df["category"].unique())),
    ]
    return "\n".join(lines)


@mcp.tool()
def spending_by_category(start_date: str = "", end_date: str = "") -> str:
    """Total spending grouped by category, with each category's share of the
    total and average transaction size. Dates are optional YYYY-MM-DD bounds;
    omit both to cover the full history."""
    df = spend_only(load())
    if start_date:
        df = df[df["date"] >= pd.Timestamp(start_date)]
    if end_date:
        df = df[df["date"] <= pd.Timestamp(end_date)]
    if df.empty:
        return "No spending found in that date range."

    g = df.groupby("category")["amount"].agg(["sum", "count", "mean"])
    g = g.sort_values("sum", ascending=False)
    total = g["sum"].sum()

    out = [f"Spending by category ({len(df)} transactions, {money(total)} total)", ""]
    out.append(f"{'Category':<16}{'Total':>12}{'Share':>9}{'Txns':>7}{'Avg':>10}")
    for cat, row in g.iterrows():
        out.append(
            f"{cat:<16}{money(row['sum']):>12}{row['sum']/total:>8.1%}"
            f"{int(row['count']):>7}{money(row['mean']):>10}"
        )
    return "\n".join(out)


@mcp.tool()
def monthly_trend(category: str = "") -> str:
    """Month-by-month spending totals with the percent change from the prior
    month. Pass a category name to trend a single category, or leave blank for
    all spending combined. Use this for questions about direction over time."""
    df = spend_only(load())
    if category:
        df = df[df["category"].str.lower() == category.lower()]
        if df.empty:
            return f"No spending found for category '{category}'."

    s = df.groupby("month")["amount"].sum().sort_index()
    pct = s.pct_change()

    label = category or "all categories"
    out = [f"Monthly spending trend ({label})", ""]
    for m in s.index:
        change = "" if pd.isna(pct[m]) else f"{pct[m]:+7.1%}"
        out.append(f"{m}  {money(s[m]):>12}  {change}")
    out += ["", f"Mean: {money(s.mean())}   Median: {money(s.median())}   "
                f"Std dev: {money(s.std())}"]
    return "\n".join(out)


@mcp.tool()
def top_merchants(limit: int = 10, category: str = "") -> str:
    """Rank merchants by total amount spent, optionally within one category.
    Useful for finding where money is concentrated."""
    df = spend_only(load())
    if category:
        df = df[df["category"].str.lower() == category.lower()]
        if df.empty:
            return f"No spending found for category '{category}'."

    g = df.groupby("merchant")["amount"].agg(["sum", "count"])
    g = g.sort_values("sum", ascending=False).head(max(1, limit))

    out = [f"Top {len(g)} merchants" + (f" in {category}" if category else ""), ""]
    for merchant, row in g.iterrows():
        out.append(f"{merchant:<24}{money(row['sum']):>12}  ({int(row['count'])} txns)")
    return "\n".join(out)


@mcp.tool()
def detect_anomalies(z_threshold: float = 2.5, limit: int = 15) -> str:
    """Find unusually large transactions using a per-category z-score, so a
    $200 grocery run and a $200 coffee are judged against different baselines.
    Lower z_threshold to surface more results. Use this for questions about
    unusual, surprising, or out-of-character spending."""
    df = spend_only(load())
    stats = df.groupby("category")["amount"].agg(["mean", "std"])
    df = df.join(stats, on="category")
    df = df[df["std"].notna() & (df["std"] > 0)]
    df["z"] = (df["amount"] - df["mean"]) / df["std"]

    hits = df[df["z"] >= z_threshold].sort_values("z", ascending=False).head(max(1, limit))
    if hits.empty:
        return (f"No transactions exceeded {z_threshold} standard deviations "
                f"above their category mean. Try a lower z_threshold.")

    out = [f"{len(hits)} anomalous transaction(s) at z >= {z_threshold}", ""]
    for _, r in hits.iterrows():
        out.append(
            f"{r['date']:%Y-%m-%d}  {r['merchant']:<22}{money(r['amount']):>10}  "
            f"z={r['z']:.1f}  ({r['category']} avg {money(r['mean'])})"
        )
    return "\n".join(out)


@mcp.tool()
def category_month_matrix() -> str:
    """A category-by-month pivot table of spending totals. Use this when the
    question calls for comparing several categories across several months at
    once, rather than one slice at a time."""
    df = spend_only(load())
    pivot = df.pivot_table(index="category", columns="month",
                           values="amount", aggfunc="sum", fill_value=0)
    pivot = pivot.round(0).astype(int)
    return "Spending by category and month (dollars)\n\n" + pivot.to_string()


if __name__ == "__main__":
    log(f"finance-analyzer starting, reading {CSV_PATH}")
    mcp.run(transport="stdio")