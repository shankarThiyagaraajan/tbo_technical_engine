"""The research report as Markdown: one section per strategy, out-of-sample shown on its own, with the data's real
coverage and the caveats (placeholder costs, too few trades) at the top where nobody can miss them."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date

from tbo_technical_engine.backtest.study import Study
from tbo_technical_engine.schemas.backtest import BacktestSettings, Metrics, WalkForward
from tbo_technical_engine.schemas.costs import CostSchedule


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


def _index_line(studies: Mapping[str, Study]) -> list[str]:
    """Whether `vwap_pivot` ran with its index filter (review 2.4: a run without index candles must say so)."""
    pivot = studies.get("vwap_pivot")
    if pivot is None:
        return []
    if not pivot.index_filter:
        return [
            "- **Index filter off**: the research cache has no nifty_50 index candles (`scripts/fill_research_cache.py "
            "--indices`), so `vwap_pivot` ran as live does without index data: with `require_index: true` it takes no "
            "trade, with `false` it trades without the index check."
        ]
    return ["- Index filter on: `vwap_pivot` read nifty_50's side of its VWAP from the cached index candles (ADR-019)."]


def render(
    studies: Mapping[str, Study],
    *,
    start: date,
    end: date,
    settings: BacktestSettings,
    costs: CostSchedule,
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
    lines += _caveats(studies, settings, costs)
    lines += ["## Summary", "", HEAD]
    for name, s in studies.items():
        low = " (low sample)" if s.metrics.trade_count < settings.evidence.min_trades else ""
        lines.append(_row(f"{name} - whole period{low}", s.metrics))
        oos = s.walk_forward.out_of_sample
        low = " (low sample)" if oos.trade_count < settings.evidence.min_trades else ""
        lines.append(_row(f"{name} - **out of sample**{low}", oos))
    lines.append("")
    for name, s in studies.items():
        lines += _section(name, s, settings)
    return "\n".join(lines) + "\n"


def _caveats(studies: Mapping[str, Study], settings: BacktestSettings, costs: CostSchedule) -> list[str]:
    """The "Read this first" bullets: placeholder costs, what out of sample means, low samples, the index filter and
    each strategy's coverage."""
    lines = []
    if costs.status != "confirmed":
        lines.append(
            f"- **Costs are a placeholder** (`{costs.schedule_version}`; {costs.source}). "
            "Replace the figures in `config/costs.yaml` with your broker's before relying on any expectancy below."
        )
    lines.append(
        "- **Out-of-sample** rows are the walk-forward test windows only: parameters were chosen on the earlier "
        "window and never saw these days. In-sample rows are shown only to compare with them."
    )
    lines.append(f"- A strategy with fewer than {settings.evidence.min_trades} trades is marked `low sample`.")
    lines += _index_line(studies)
    for name, s in studies.items():
        c = s.coverage
        skipped = f" Grid skipped: {s.grid_skipped}." if s.grid_skipped else ""
        lines.append(f"- `{name}`: {c.isins_with_data} of {c.requested_isins} stocks had candles; {c.note}.{skipped}")
    lines.append("")
    return lines


def _section(name: str, s: Study, settings: BacktestSettings) -> list[str]:
    """One strategy's section: folds, stability, return statistics, the three result rows and each fold."""
    wf = s.walk_forward
    if wf.stability_pct is not None:
        stability = (
            f"Parameter stability: {wf.stability_pct}% of folds had neighbours within "
            f"{settings.walk_forward.stability_band} of the chosen set."
        )
    else:
        stability = "No parameter grid was walked."
    stats = s.statistics
    lines = [f"## {name}", ""]
    lines.append(
        f"Folds: {len(wf.folds)} (train {settings.walk_forward.train_days} sessions, "
        f"test {settings.walk_forward.test_days}). " + stability
    )
    lines.append(
        f"Return statistics on the reference capital: total {stats['total_return_pct']}%, "
        f"Sharpe {stats['sharpe']}, max drawdown {stats['max_drawdown_pct']}%."
    )
    lines += ["", HEAD, _row("whole period", s.metrics), _row("in sample (train windows)", wf.in_sample)]
    lines += [_row("**out of sample (test windows)**", wf.out_of_sample), ""]
    lines += _folds(wf) if wf.folds else _NO_FOLD
    if wf.parameter_choices:
        lines.append(
            "Chosen values across folds: "
            + "; ".join(
                f"{p}: " + ", ".join(f"{v} x{n}" for v, n in sorted(c.items())) for p, c in wf.parameter_choices.items()
            )
        )
        lines.append("")
    return lines


_NO_FOLD = ["No walk-forward fold: the data covers fewer sessions than one train plus one test window.", ""]


def _stable(value: bool | None) -> str:
    """A fold's stability as the table shows it."""
    if value is None:
        return "-"
    return "yes" if value else "no"


def _folds(wf: WalkForward) -> list[str]:
    """The table of walk-forward folds."""
    lines = [
        "| test window | chosen | train expectancy R | test trades | test expectancy R | stable |",
        "|---|---|---|---|---|---|",
    ]
    for f in wf.folds:
        chosen = ", ".join(f"{k}={v}" for k, v in f.chosen.items()) or "fixed"
        lines.append(
            f"| {f.test_from} to {f.test_to} | {chosen} | {f.train.expectancy_r} | {f.test.trade_count} "
            f"| {f.test.expectancy_r} | {_stable(f.stable)} |"
        )
    lines.append("")
    return lines
