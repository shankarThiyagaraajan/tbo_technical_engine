# tbo_technical_engine progress

Plan: `tbo_plans/tbo_technical_engine/PLAN.md` in the project's shared folder. What is built, what is not, decisions.

## Status

| Phase | State |
|---|---|
| TE0 Repository foundation | Built (branch `claude/technical-te0-te4-1mrk0g`). Generated from the tbo_platform service template at `bfaaabf`; the contract is vendored (`v1.2.0`); all 12 operations are protected stubs answering 501; Celery with the outbox sweep; research queue guarded by the kit's mode blackout; signal worker skeleton. Checked locally only (CI did not run). |
| TE1 Market feed integration | Not started |
| TE2 Indicator library | Not started |
| TE3 Intraday strategy engine | Not started |
| TE4 Market context | Not started |
| TE5 to TE8 | Out of scope for the current build (TE5 needs the broker cost figures) |

## Decisions

- Pins: tbo_contracts tag `v1.2.0` and tbo_platform commit `bfaaabf` (no platform tag yet). The kit's own sources still pin
  tbo_contracts v1.1.0, so `[tool.uv] override-dependencies` forces v1.2.0; drop it when the kit moves.
- Operations whose phase is not built answer 501 `not_implemented`; Schemathesis tolerates 501 until the last stub goes.
- Process entry points live in the package (`tbo_technical_engine.apps.*`) so the image ships them; the plan's top-level `apps/` is this.
- The old empty `tbot_technical_engine` repository is not used.

## Open items
