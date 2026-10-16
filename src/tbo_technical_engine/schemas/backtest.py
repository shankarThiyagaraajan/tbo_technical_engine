"""Backtest requests, settings, trades and results.

`BacktestCreate` is the request body (strict, like the contract). The rest is internal: the settings file, one
simulated trade, and the numbers a run produces.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from tbo_technical_engine.schemas.candle import Candle

NAME = re.compile(r"^[a-z][a-z0-9]*(_[a-z0-9]+)*$")
ISIN = re.compile(r"^IN[A-Z0-9]{9}[0-9]$")
ExitReason = Literal["target_2", "stop", "stop_at_entry", "time_exit", "end_of_data"]
Side = Literal["buy", "sell"]


class ParamOverride(BaseModel):
    """One strategy parameter to change for a backtest run."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(max_length=60)
    value: str = Field(max_length=60)

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        if not NAME.fullmatch(value):
            raise ValueError("not a parameter name")
        return value


class BacktestCreate(BaseModel):
    """`POST /v1/backtests`. The size caps (ISINs, date range) are in `config/backtest.yaml`, checked by the API."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    strategy: str = Field(max_length=60)
    params_version: str | None = Field(default=None, min_length=1, max_length=40)
    params_overrides: list[ParamOverride] = Field(default_factory=list, max_length=30)
    from_date: date
    to_date: date
    isins: list[str] = Field(default_factory=list, max_length=500)

    @field_validator("strategy")
    @classmethod
    def _strategy(cls, value: str) -> str:
        if not NAME.fullmatch(value):
            raise ValueError("not a strategy name")
        return value

    @field_validator("isins")
    @classmethod
    def _isins(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value):
            raise ValueError("ISINs must be unique")
        bad = [i for i in value if not ISIN.fullmatch(i)]
        if bad:
            raise ValueError(f"not an ISIN: {bad[0]}")
        return value


class Caps(BaseModel):
    """Hard limits on one backtest request (stocks, days, stock-days, concurrent jobs)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    max_isins: int = Field(ge=1, le=500)
    max_range_days: int = Field(ge=1)
    max_stock_days: int = Field(ge=1)
    max_concurrent_jobs: int = Field(ge=1)
    max_runtime_seconds: int = Field(ge=60, le=3500)


class Execution(BaseModel):
    """Fill and exit assumptions of the simulation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    notional_per_trade: Decimal = Field(gt=0)
    slippage_bps: Decimal = Field(ge=0)
    entry_wait_bars: int = Field(ge=1, le=6)
    t1_fraction: Decimal = Field(gt=0, lt=1)
    stop_to_entry_after_t1: bool
    square_off: str
    one_position_per_stock: bool
    same_bar_rule: Literal["stop_first"]


class Reporting(BaseModel):
    """Reference capital used only for return statistics."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    reference_capital: Decimal = Field(gt=0)
    """Rupees. Only turns daily profit into a return for Sharpe and drawdown %; it never changes a trade."""


class WalkForwardSettings(BaseModel):
    """Window lengths and step of the walk-forward folds."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    train_days: int = Field(ge=5)
    test_days: int = Field(ge=1)
    min_trades_train: int = Field(ge=1)
    stability_band: Decimal = Field(gt=0, le=1)


class EvidenceSettings(BaseModel):
    """What a strategy needs before its evidence counts (minimum trades)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    min_trades: int = Field(ge=1)


class BacktestSettings(BaseModel):
    """`config/backtest.yaml`. Stored with every result, because changing a value changes what a backtest means."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    caps: Caps
    execution: Execution
    reporting: Reporting
    walk_forward: WalkForwardSettings
    evidence: EvidenceSettings
    grids: dict[str, dict[str, list[str]]] = Field(default_factory=dict)


@dataclass(frozen=True)
class StockData:
    """The candles and daily history of one stock for the days under test."""

    five: Sequence[Candle]
    fifteen: Sequence[Candle]
    daily: Sequence[Candle]


@dataclass(frozen=True)
class Trade:
    """One simulated trade, entry to final exit. `r` is net of costs and slippage, in units of the initial risk."""

    isin: str
    side: Side
    signal_id: str
    entry_at: datetime
    exit_at: datetime
    entry_price: Decimal
    exit_price: Decimal
    """The price of the last exit (the remainder after target 1, if there was one)."""
    exit_reason: ExitReason
    quantity: int
    gross_pnl: Decimal
    costs: Decimal
    r: Decimal
    """Net result (after charges) divided by the risk: (entry to stop) x quantity."""
    gross_r: Decimal
    """The same before charges (slippage is already in the prices)."""
    target_1_hit: bool


@dataclass(frozen=True)
class Metrics:
    """Trade statistics in R, after charges unless a field says otherwise."""

    trade_count: int
    win_rate_pct: Decimal
    avg_r: Decimal
    """Mean R per trade BEFORE charges (slippage is in the prices): the strategy's edge without the broker."""
    expectancy_r: Decimal
    """Mean R per trade AFTER charges: what the owner would have kept. The number to judge a strategy by."""
    max_drawdown_r: Decimal
    profit_factor: Decimal | None
    total_r: Decimal
    total_costs: Decimal
    gross_pnl: Decimal


@dataclass(frozen=True)
class Coverage:
    """What data the run really had. A report over 12 months on 3 months of candles must say so."""

    requested_isins: int
    isins_with_data: int
    first_candle_date: date | None
    last_candle_date: date | None
    trading_days: int
    """Distinct session dates with at least one 5-minute candle inside [from_date, to_date]."""
    skipped_no_data: int
    gate_applied: bool
    note: str


@dataclass(frozen=True)
class Fold:
    """One walk-forward fold: the parameters chosen on train and how they did on test."""

    train_from: date
    train_to: date
    test_from: date
    test_to: date
    chosen: dict[str, str]
    """The parameter values chosen on the train window (empty without a grid)."""
    train: Metrics
    test: Metrics
    stable: bool | None
    """The chosen set's neighbours were about as good on the train window (`stability_band`); None without a grid."""


@dataclass(frozen=True)
class WalkForward:
    """All folds plus the pooled out-of-sample figures and whether the choice was stable."""

    folds: list[Fold]
    out_of_sample: Metrics
    """All test windows pooled; never mixed with train results."""
    in_sample: Metrics
    stability_pct: Decimal | None
    """Share of folds whose chosen set was stable; None without a grid."""
    parameter_choices: dict[str, dict[str, int]]
    """For each grid parameter, how often each value was chosen across the folds."""
