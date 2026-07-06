from __future__ import annotations

import os
import sys


def _service_from_argv(default: str = "service") -> str:
    executable = os.path.basename(sys.argv[0])
    return executable.removesuffix("-migrate").removesuffix("-fixtures") or default


def migrate_service(service_name: str | None = None) -> None:
    service_name = service_name or _service_from_argv()
    os.environ["SERVICE_MIGRATION_MODE"] = "1"

    from app.shared.core import database

    database.import_all_models()
    selected_tables = database._init_db_tables()
    tables = sorted(table.name for table in selected_tables) if selected_tables else sorted(database.Base.metadata.tables)
    database.init_db()
    print(f"{service_name}: migrated tables {', '.join(tables)}")


def fixtures_service(service_name: str | None = None) -> None:
    service_name = service_name or _service_from_argv()
    print(f"{service_name}: no default fixtures to load")


def migrate() -> None:
    migrate_service()


def fixtures() -> None:
    fixtures_service()
