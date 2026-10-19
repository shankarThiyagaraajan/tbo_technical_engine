"""`config/swing.yaml` as typed records, and the snapshot and setup records the swing analysis produces.

The file is strict (an unknown key is an error), so a misspelled parameter stops the process at start instead of
silently changing what a rule means.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class TrendWeights(_Strict):
    """Weights of the trend score parts."""

    close_above_fast: float = Field(ge=0)
    close_above_mid: float = Field(ge=0)
    fast_above_mid: float = Field(ge=0)
    mid_above_slow: float = Field(ge=0)
    mid_rising: float = Field(ge=0)
    di_bullish: float = Field(ge=0)


class TrendParams(_Strict):
    """Trend score thresholds and ADX gating."""

    fast: int = Field(ge=2)
    mid: int = Field(ge=2)
    slow: int = Field(ge=2)
    slope_bars: int = Field(ge=1)
    adx_period: int = Field(ge=2)
    adx_min: float = Field(ge=0)
    adx_full: float = Field(gt=0)
    up_at: float = Field(gt=50, le=100)
    down_at: float = Field(ge=0, lt=50)
    weights: TrendWeights


class RelativeStrengthParams(_Strict):
    """Relative-strength lookback."""

    weight_20d: float = Field(ge=0)
    weight_60d: float = Field(ge=0)


class LevelParams(_Strict):
    """Support and resistance zone detection settings."""

    pivot_wing: int = Field(ge=1)
    lookback: int = Field(ge=20)
    cluster_atr: float = Field(gt=0)
    min_touches: int = Field(ge=1)
    band_atr: float = Field(ge=0)
    max_levels: int = Field(ge=1, le=20)  # the contract allows 20


class VolatilityParams(_Strict):
    """Volatility regime and squeeze settings."""

    atr_period: int = Field(ge=2)
    percentile_window: int = Field(ge=20)
    min_points: int = Field(ge=2)
    contracting_below: float = Field(ge=0, le=100)
    expanding_above: float = Field(ge=0, le=100)
    bollinger_period: int = Field(ge=2)
    bollinger_k: float = Field(gt=0)
    squeeze_window: int = Field(ge=20)
    squeeze_percentile: float = Field(ge=0, le=100)
    squeeze_min_points: int = Field(ge=2)


class LiquidityParams(_Strict):
    """Liquidity metric settings."""

    turnover_days: int = Field(ge=1)
    spread_days: int = Field(ge=2)
    spread_min_pairs: int = Field(ge=1)


SetupType = Literal["base_breakout", "ema_pullback", "week_52_high_breakout"]


class BaseBreakoutParams(_Strict):
    """Base breakout detector settings."""

    base_bars: int = Field(ge=5)
    max_depth_pct: float = Field(gt=0)
    volume_ratio_min: float = Field(gt=0)
    close_above_pct: float = Field(ge=0)
    stop_buffer_atr: float = Field(ge=0)
    target_1_r: float = Field(gt=0)
    target_2_r: float = Field(gt=0)


class EmaPullbackParams(_Strict):
    """Pullback to the 20/50 EMA detector settings."""

    ema: list[int] = Field(min_length=1)
    touch_pct: float = Field(ge=0)
    pullback_bars: int = Field(ge=2)
    min_pullback_pct: float = Field(ge=0)
    stop_buffer_atr: float = Field(ge=0)
    target_1_r: float = Field(gt=0)
    target_2_r: float = Field(gt=0)


class Week52Params(_Strict):
    """52-week high breakout detector settings."""

    lookback: int = Field(ge=20)
    min_bars: int = Field(ge=20)
    volume_ratio_min: float = Field(gt=0)
    close_in_range_min: float = Field(ge=0, le=1)
    stop_atr: float = Field(gt=0)
    target_1_r: float = Field(gt=0)
    target_2_r: float = Field(gt=0)


class SetupParams(_Strict):
    """Settings shared by the setup detectors."""

    valid_days: int = Field(ge=1, le=10)
    risk_atr_min: float = Field(ge=0)
    risk_atr_max: float = Field(gt=0)
    precedence: list[SetupType]
    base_breakout: BaseBreakoutParams
    ema_pullback: EmaPullbackParams
    week_52_high_breakout: Week52Params

    @field_validator("precedence")
    @classmethod
    def _each_once(cls, value: list[SetupType]) -> list[SetupType]:
        if sorted(value) != sorted(["base_breakout", "ema_pullback", "week_52_high_breakout"]):
            raise ValueError("precedence must name each setup type exactly once")
        return value


class SwingParams(_Strict):
    """The whole of config/swing.yaml."""

    params_version: str = Field(min_length=1, max_length=40)
    history_days: int = Field(ge=300)
    turnover_lookback_days: int = Field(ge=30)
    min_bars: int = Field(ge=61)
    fetch_concurrency: int = Field(ge=1, le=32)
    max_failed_share: float = Field(ge=0, le=1)
    max_stale_share: float = Field(ge=0, le=1)
    trend: TrendParams
    relative_strength: RelativeStrengthParams
    levels: LevelParams
    volatility: VolatilityParams
    liquidity: LiquidityParams
    setups: SetupParams

    @model_validator(mode="after")
    def _pullback_emas_exist(self) -> SwingParams:
        known = {self.trend.fast, self.trend.mid}
        unknown = sorted(set(self.setups.ema_pullback.ema) - known)
        if unknown:
            raise ValueError(f"ema_pullback.ema {unknown} are not trend.fast or trend.mid ({sorted(known)})")
        return self


class Level(BaseModel):
    """One support or resistance zone in a snapshot."""

    model_config = ConfigDict(frozen=True)

    kind: Literal["support", "resistance"]
    low: str
    high: str
    source: Literal["swing_cluster", "week_52_high", "week_52_low", "prior_month_high", "prior_month_low"]


class Snapshot(BaseModel):
    """`technical_snapshot.v1`: the daily multi-day view of one stock. Decimals are strings (the contract's Decimal)."""

    model_config = ConfigDict(frozen=True)

    isin: str
    as_of_date: str
    trend_state: Literal["up", "down", "sideways"]
    trend_score: int = Field(ge=0, le=100)
    rs_percentile: int = Field(ge=0, le=100)
    return_20d_pct: str
    return_60d_pct: str
    levels: list[Level]
    atr_pct: str
    atr_pct_percentile: int = Field(ge=0, le=100)
    vol_regime: Literal["contracting", "normal", "expanding"]
    is_squeeze: bool
    avg_turnover_20d: str
    spread_est_pct: str | None
    computed_at: str
    return_12m_pct: str | None = None


class Setup(BaseModel):
    """`swing_setup.v1`: a multi-day setup found after a session's close."""

    model_config = ConfigDict(frozen=True)

    setup_id: str
    isin: str
    setup_date: date
    setup_type: SetupType
    side: Literal["buy"]
    entry: Decimal
    stop: Decimal
    target_1: Decimal
    target_2: Decimal | None
    valid_until_date: date
    params_version: str
    created_at: datetime
