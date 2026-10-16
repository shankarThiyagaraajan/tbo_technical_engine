"""Metrics from hand-made trades, and the vectorbt return statistics."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from tbo_platform_kit.calendar import IST

from tbo_technical_engine.backtest.metrics import summarize
from tbo_technical_engine.backtest.stats import equity_stats
from tbo_technical_engine.schemas.backtest import Trade
from tests.backtest.builders import at

D = Decimal


def trade(n: int, r: str, gross_r: str | None = None, costs: str = "0", pnl: str | None = None) -> Trade:
    return Trade(
        isin="INE002A01018",
        side="buy",
        signal_id=f"id{n:03d}",
        entry_at=at(1 + n, date(2026, 10, 1) + timedelta(days=n)),
        exit_at=at(2 + n, date(2026, 10, 1) + timedelta(days=n)),
        entry_price=D("100"),
        exit_price=D("101"),
        exit_reason="stop",
        quantity=10,
        gross_pnl=D(pnl if pnl is not None else r) * 100,
        costs=D(costs),
        r=D(r),
        gross_r=D(gross_r if gross_r is not None else r),
        target_1_hit=False,
    )


def test_metrics_on_five_trades() -> None:
    m = summarize([trade(1, "2"), trade(2, "-1"), trade(3, "-1"), trade(4, "1.5"), trade(5, "-1")])
    assert m.trade_count == 5
    assert m.win_rate_pct == D("40.00")
    assert m.expectancy_r == D("0.1000")  # 0.5 / 5
    assert m.total_r == D("0.5000")
    assert m.profit_factor == D("1.17")  # 3.5 / 3
    assert m.max_drawdown_r == D("2.0000")  # from +2 down to 0 after two losses, then to -1 after the third


def test_drawdown_is_peak_to_trough_of_the_running_r() -> None:
    m = summarize([trade(1, "1"), trade(2, "-3"), trade(3, "1"), trade(4, "1")])
    assert m.max_drawdown_r == D("3.0000")


def test_avg_r_is_before_charges_and_expectancy_after() -> None:
    m = summarize([trade(1, "0.8", gross_r="1.0"), trade(2, "-1.2", gross_r="-1.0")])
    assert (m.avg_r, m.expectancy_r) == (D("0.0000"), D("-0.2000"))


def test_no_losing_trade_means_no_profit_factor() -> None:
    assert summarize([trade(1, "1"), trade(2, "2")]).profit_factor is None


def test_no_trades_is_zeros() -> None:
    m = summarize([])
    assert (m.trade_count, m.expectancy_r, m.max_drawdown_r, m.profit_factor) == (0, Decimal(0), Decimal(0), None)


def test_equity_stats_count_idle_days_as_zero_returns() -> None:
    # One profitable day (+Rs 10,000 on Rs 10,00,000 = 1%) among 10 sessions.
    sessions = [date(2026, 10, 1) + timedelta(days=i) for i in range(10)]
    t = trade(3, "1", pnl="100")  # gross_pnl 10,000; exits on sessions[3] (IST date of exit_at)
    assert t.exit_at.astimezone(IST).date() == sessions[3]
    stats = equity_stats([t], sessions, D(1_000_000))
    assert stats["total_return_pct"] == 1.0
    assert stats["max_drawdown_pct"] == 0.0
    assert stats["sharpe"] is not None and stats["sharpe"] > 0


def test_equity_stats_without_sessions_say_nothing() -> None:
    assert equity_stats([], [], D(1_000_000)) == {"total_return_pct": None, "sharpe": None, "max_drawdown_pct": None}


def test_a_loss_shows_as_a_drawdown() -> None:
    sessions = [date(2026, 10, 1) + timedelta(days=i) for i in range(5)]
    stats = equity_stats([trade(2, "-1", pnl="-50")], sessions, D(1_000_000))  # -Rs 5,000 = -0.5%
    assert stats["total_return_pct"] == -0.5 and stats["max_drawdown_pct"] == 0.5


def test_a_sharpe_that_is_not_defined_is_none_not_infinity() -> None:
    # Every session returns exactly zero: no volatility, so the ratio is 0/0.
    sessions = [date(2026, 10, 1) + timedelta(days=i) for i in range(5)]
    assert equity_stats([], sessions, D(1_000_000))["sharpe"] is None
