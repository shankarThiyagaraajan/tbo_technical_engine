"""Guard against corporate actions the engine cannot see (review 9.5)."""

MAX_OPENING_GAP = 0.25
"""An opening gap bigger than this against the previous session's close (25 %) is read as a split or bonus, not a move.

Why 25 %: no stock opens more than 20 % away in a normal session (circuit limits), while a 1:2 split is -50 %, a
1:1 bonus -50 % and a 1:5 split -80 %. Intraday candles are not adjusted, so on such a day the pivots, CPR and gap come
from pre-split prices and every level is nonsense: the stock is not evaluated that day. Small ratios (5:4 bonus, -11 %)
slip under this line; the fundamental gate's ex-date flag covers them at the decision engine."""
