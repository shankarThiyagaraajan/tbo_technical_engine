"""Alembic environment: every migration runs in schema `technical` with the component's own role.

Migrations must stay backward compatible for one release (a rollback redeploys the previous image on the new schema).
"""

from __future__ import annotations

from alembic import context
from tbo_platform_kit.db import run_migrations

from tbo_technical_engine.settings import SHORT_NAME, Settings
from tbo_technical_engine.tables import metadata

settings = Settings()
settings.require("database_url")
assert settings.database_url is not None
run_migrations(context, target_metadata=metadata, schema=SHORT_NAME, url=settings.database_url.get_secret_value())
