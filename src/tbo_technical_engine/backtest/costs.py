"""What a trade costs in rupees: brokerage, STT, exchange charge, SEBI, stamp duty, IPFT and GST on the fees.

Pure: a schedule (`config/costs.yaml`), a side, a price and a quantity in, rupees out. Slippage is not here; it moves
the fill price in `simulate`, so it shows in the gross result and is not counted twice.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import yaml

from tbo_technical_engine.schemas.backtest import Side
from tbo_technical_engine.schemas.costs import CostSchedule

HUNDRED = Decimal(100)
CRORE = Decimal(10_000_000)


def load_costs(path: Path) -> CostSchedule:
    """Read and validate the charge schedule file; a malformed file stops the run, never a guess."""
    return CostSchedule.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def leg_charges(schedule: CostSchedule, side: Side, price: Decimal, quantity: int) -> Decimal:
    """All charges for one executed order, unrounded (rounded once, on the sum of a whole trade).

    Example (placeholder schedule): buying 100 at 500.00 is a 50,000 turnover; brokerage 0.03% = 15.00 (under the 20
    cap), exchange 0.00307% = 1.535, SEBI 0.05, IPFT 0.00005, stamp 0.003% = 1.50, GST 18% of 16.58505 = 2.985309;
    the total is 21.070359. Selling the same adds STT 0.025% = 12.50 instead of the stamp duty: 32.070359.
    """
    turnover = price * quantity
    brokerage = min(turnover * schedule.brokerage_pct / HUNDRED, schedule.brokerage_cap)
    exchange = turnover * schedule.exchange_txn_pct / HUNDRED
    sebi = turnover * schedule.sebi_per_crore / CRORE
    ipft = turnover * schedule.ipft_per_crore / CRORE
    gst = (brokerage + exchange + sebi + ipft) * schedule.gst_pct / HUNDRED
    stt = turnover * schedule.stt_sell_pct / HUNDRED if side == "sell" else Decimal(0)
    stamp = turnover * schedule.stamp_buy_pct / HUNDRED if side == "buy" else Decimal(0)
    return brokerage + exchange + sebi + ipft + gst + stt + stamp
