"""Swing analysis on daily candles: trend, relative strength, levels, volatility, liquidity and the three setup
detectors. Pure code (arrays in, records out): no clock, no database, no network, so a stock gives the same answer
in the daily job, in a test and in the backtester.

The rule for every function here: the last bar is the session the analysis is as of, and nothing after it exists.
"""
