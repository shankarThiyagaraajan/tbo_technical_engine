"""TE5: backtesting and research. Replays candles through the live strategy code (`replay`), turns each candidate into a
simulated trade (`simulate`, `costs`), and summarises them (`metrics`, `walkforward`, `evidence`).

Nothing here talks to the network or the database; `services/backtests.py` wires it to storage and the queue.
"""
