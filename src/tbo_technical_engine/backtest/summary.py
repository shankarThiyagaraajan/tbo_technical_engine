"""The research report as Markdown: one section per strategy, out-of-sample shown on its own, with the data's real
coverage and the caveats (unconfirmed costs, too few trades) at the top where nobody can miss them."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date

from tbo_costs import ScheduleBook

from tbo_technical_engine.backtest.study import Study
from tbo_technical_engine.schemas.backtest import BacktestSettings, Metrics


def _row(label: str, m: Metrics) -> str:
    pf = "-" if m.profit_factor is None else str(m.profit_factor)
    return (
        f"| {label} | {m.trade_count} | {m.win_rate_pct} | {m.avg_r} | {m.expectancy_r} | {m.max_drawdown_r} | {pf} |"
    )


HEAD = (
    "| | trades | win % | avg R (before charges) | expectancy R (after charges) | max drawdown R | profit factor |\n"
    "|---|---|---|---|---|---|---|"
)
FOLD_HEAD = "\n".join(
    [
        "| test window | chosen | train expectancy R | test trades | test expectancy R | stable |",
        "|---|---|---|---|---|---|",
    ]
)


def render(
    studies: Mapping[str, Study],
    *,
    start: date,
    end: date,
    settings: BacktestSettings,
    costs: ScheduleBook,
    title: str = "Backtest report",
    banner: str | None = None,
) -> str:
    """The Markdown evidence report for a set of studies, with coverage and the out-of-sample figures first."""
    lines = [f"# {title}", ""]
    if banner:
        lines += [f"> **{banner}**", ""]
    lines += [
        f"Requested period: {start} to {end}. Parameters: the values in `config/strategies.yaml`; "
        "execution and walk-forward settings from `config/backtest.yaml` "
        f"(slippage {settings.execution.slippage_bps} bps each way, {settings.execution.notional_per_trade} rupees "
        f"a trade, T1 sells {settings.execution.t1_fraction}; a candle touching stop and target counts as the stop).",
        "",
        "## Read this first",
        "",
    ]
    if costs.status != "confirmed":
        entry = costs.entries[-1]
        lines.append(
            f"- **Costs are {costs.status}** (`{entry.schedule_version}`, digest `{costs.digest()}`; {entry.source}). "
            "They are approximate until the schedule is checked against the broker's own calculator; "
            "do not rely on any expectancy below to the paisa."
        )
    lines.append(
        "- **Out-of-sample** rows are the walk-forward test windows only: parameters were chosen on the earlier "
        "window and never saw these days. In-sample rows are shown only to compare with them."
    )
    lines.append(f"- A strategy with fewer than {settings.evidence.min_trades} trades is marked `low sample`.")
    for name, s in studies.items():
        c = s.coverage
        lines.append(
            f"- `{name}`: {c.isins_with_data} of {c.requested_isins} stocks had candles; {c.note}."
            + (f" Grid skipped: {s.grid_skipped}." if s.grid_skipped else "")
        )
    lines.append("")
    lines += ["## Summary", "", HEAD]
    for name, s in studies.items():
        low = " (low sample)" if s.metrics.trade_count < settings.evidence.min_trades else ""
        lines.append(_row(f"{name} - whole period{low}", s.metrics))
        oos = s.walk_forward.out_of_sample
        low = " (low sample)" if oos.trade_count < settings.evidence.min_trades else ""
        lines.append(_row(f"{name} - **out of sample**{low}", oos))
    lines.append("")
    for name, s in studies.items():
        wf = s.walk_forward
        lines += [f"## {name}", ""]
        lines.append(
            f"Folds: {len(wf.folds)} (train {settings.walk_forward.train_days} sessions, "
            f"test {settings.walk_forward.test_days}). "
            + (
                f"Parameter stability: {wf.stability_pct}% of folds had neighbours within "
                f"{settings.walk_forward.stability_band} of the chosen set."
                if wf.stability_pct is not None
                else "No parameter grid was walked."
            )
        )
        stats = s.statistics
        lines.append(
            f"Return statistics on the reference capital: total {stats['total_return_pct']}%, "
            f"Sharpe {stats['sharpe']}, max drawdown {stats['max_drawdown_pct']}%."
        )
        lines += ["", HEAD, _row("whole period", s.metrics), _row("in sample (train windows)", wf.in_sample)]
        lines += [_row("**out of sample (test windows)**", wf.out_of_sample), ""]
        if wf.folds:
            lines += [
                "| test window | chosen | train expectancy R | test trades | test expectancy R | stable |",
                "|---|---|---|---|---|---|",
            ]
            for f in wf.folds:
                chosen = ", ".join(f"{k}={v}" for k, v in f.chosen.items()) or "fixed"
                stable = "-" if f.stable is None else ("yes" if f.stable else "no")
                lines.append(
                    f"| {f.test_from} to {f.test_to} | {chosen} | {f.train.expectancy_r} | {f.test.trade_count} "
                    f"| {f.test.expectancy_r} | {stable} |"
                )
            lines.append("")
        else:
            lines += ["No walk-forward fold: the data covers fewer sessions than one train plus one test window.", ""]
        if wf.parameter_choices:
            lines.append(
                "Chosen values across folds: "
                + "; ".join(
                    f"{p}: " + ", ".join(f"{v} x{n}" for v, n in sorted(c.items()))
                    for p, c in wf.parameter_choices.items()
                )
            )
            lines.append("")
    return "\n".join(lines) + "\n"
