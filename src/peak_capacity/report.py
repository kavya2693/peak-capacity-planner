"""Markdown copy of every results table, so the README can quote them without rounding by hand."""

from __future__ import annotations

from pathlib import Path

import pandas as pd


def md_table(df: pd.DataFrame, digits: int = 4) -> str:
    def fmt(v: object) -> str:
        if isinstance(v, float):
            return f"{v:,.{digits}g}" if abs(v) < 1000 else f"{v:,.0f}"
        return str(v)

    head = "| " + " | ".join(map(str, df.columns)) + " |"
    sep = "|" + "---|" * len(df.columns)
    body = ["| " + " | ".join(fmt(v) for v in row) + " |" for row in df.itertuples(index=False)]
    return "\n".join([head, sep, *body])


def write_report(out: dict[str, pd.DataFrame], path: Path) -> None:
    order = ["cleaning", "forecast_accuracy", "plan_summary", "tradeoff", "by_segment"]
    parts = ["# Run report", ""]
    for name in order:
        if name in out:
            parts += [f"## {name}", "", md_table(out[name]), ""]
    path.write_text("\n".join(parts))
