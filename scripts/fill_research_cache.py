"""Kept for a checkout: the tool lives in the package so the image has it (review 9.21).

python -m tbo_technical_engine.apps.fill_research_cache --help
"""

from __future__ import annotations

from tbo_technical_engine.apps.fill_research_cache import main

if __name__ == "__main__":
    raise SystemExit(main())
