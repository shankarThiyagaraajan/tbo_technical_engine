"""Numbers from a list of trades. All in R (the initial risk of a trade), so strategies and stocks compare."""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

from tbo_technical_engine.schemas.backtest import Metrics, Trade

ZERO = Decimal(0)
FOUR = Decimal("0.0001")


def summarize(trades: Sequence[Trade]) -> Metrics:
    """Trades are taken in exit order for the drawdown. An empty list gives zeros (and no profit factor)."""
    ordered = sorted(trades, key=lambda t: (t.exit_at, t.entry_at, t.signal_id))
    n = len(ordered)
    if n == 0:
        return Metrics(0, ZERO, ZERO, ZERO, ZERO, None, ZERO, ZERO, ZERO)
    net = [t.r for t in ordered]
    wins = sum(1 for r in net if r > 0)
    gains = sum((r for r in net if r > 0), ZERO)
    losses = -sum((r for r in net if r < 0), ZERO)
    peak = equity = drawdown = ZERO
    for r in net:
        equity += r
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
    return Metrics(
        trade_count=n,
        win_rate_pct=(Decimal(wins) * 100 / n).quantize(Decimal("0.01")),
        avg_r=(sum((t.gross_r for t in ordered), ZERO) / n).quantize(FOUR),
        expectancy_r=(sum(net, ZERO) / n).quantize(FOUR),
        max_drawdown_r=drawdown.quantize(FOUR),
        profit_factor=(gains / losses).quantize(Decimal("0.01")) if losses > 0 else None,
        total_r=sum(net, ZERO).quantize(FOUR),
        total_costs=sum((t.costs for t in ordered), ZERO),
        gross_pnl=sum((t.gross_pnl for t in ordered), ZERO),
    )
