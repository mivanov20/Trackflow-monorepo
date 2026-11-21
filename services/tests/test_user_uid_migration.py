"""Migración de la columna `user_uuid` -> `user_uid` y script para crear el primer administrador."""

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.pool import StaticPool
from sqlmodel import create_engine

from migrate_user_uid import migrate
from promote_user import main as promote_main
from services import accounts, database
from services.accounts import Role


def legacy_engine():
    """Base con el esquema anterior: columna user_uuid e índice con su nombre antiguo."""
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    with engine.begin() as connection:
        for table in ("stockentry", "stockexit"):
            connection.execute(text(f"CREATE TABLE {table} (id INTEGER PRIMARY KEY, user_uuid VARCHAR NOT NULL)"))
            connection.execute(text(f"CREATE INDEX ix_{table}_user_uuid ON {table} (user_uuid)"))
            connection.execute(text(f"INSERT INTO {table} (user_uuid) VALUES ('operator-1')"))
    return engine


def columns(engine, table):
    return {column["name"] for column in inspect(engine).get_columns(table)}


def test_migration_renames_the_column_keeps_data_and_is_idempotent():
    engine = legacy_engine()
    assert migrate(engine) == ["stockentry: user_uuid -> user_uid", "stockexit: user_uuid -> user_uid"]
    for table in ("stockentry", "stockexit"):
        assert "user_uid" in columns(engine, table) and "user_uuid" not in columns(engine, table)
        with engine.connect() as connection:
            assert connection.execute(text(f"SELECT user_uid FROM {table}")).scalar() == "operator-1"
    assert migrate(engine) == []  # segunda ejecución: no cambia nada


def test_migration_skips_a_database_without_the_tables():
    engine = create_engine("sqlite://", poolclass=StaticPool)
    assert migrate(engine) == []


def test_startup_refuses_an_unmigrated_database_with_clear_instructions(monkeypatch):
    monkeypatch.setattr(database, "engine", legacy_engine())
    with pytest.raises(RuntimeError, match="migrate_user_uid.py"):
        database.ensure_user_uid_columns()


def test_startup_accepts_a_migrated_database(monkeypatch):
    engine = legacy_engine()
    migrate(engine)
    monkeypatch.setattr(database, "engine", engine)
    database.ensure_user_uid_columns()


def test_promote_user_script_sets_the_role_and_reports_errors(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("AUTH_DB_PATH", str(tmp_path / "users.json"))
    accounts.create_user("alice@example.com", "correct-horse-42")

    assert promote_main(["promote_user.py", "alice@example.com", "admin"]) == 0
    assert accounts.get_user_by_email("alice@example.com").role == Role.admin
    assert "is now admin" in capsys.readouterr().out

    assert promote_main(["promote_user.py", "nobody@example.com", "admin"]) == 1
    assert "No account" in capsys.readouterr().err
    assert promote_main(["promote_user.py", "alice@example.com", "superuser"]) == 1
    assert "Unknown role" in capsys.readouterr().err
    assert promote_main(["promote_user.py", "alice@example.com", "user"]) == 1  # última admin
    assert "without an active administrator" in capsys.readouterr().err
    assert promote_main(["promote_user.py"]) == 1


# --- usabilidad del script de migración -------------------------------------------


def test_dry_run_reports_the_changes_without_applying_them():
    engine = legacy_engine()
    assert migrate(engine, dry_run=True) == ["stockentry: user_uuid -> user_uid", "stockexit: user_uuid -> user_uid"]
    assert "user_uuid" in columns(engine, "stockentry") and "user_uid" not in columns(engine, "stockentry")
    assert len(migrate(engine)) == 2  # la ejecución real sigue funcionando después


def test_env_file_in_services_folder_supplies_database_url(tmp_path, monkeypatch):
    import migrate_user_uid

    (tmp_path / "services").mkdir()
    (tmp_path / "services" / ".env").write_text("DATABASE_URL=sqlite:///from-env-file.db\n", encoding="utf-8")
    monkeypatch.setattr(migrate_user_uid, "REPO_ROOT", tmp_path)
    monkeypatch.delenv("DATABASE_URL")  # monkeypatch lo restaura al terminar el test
    migrate_user_uid.load_env_file()
    import os

    assert os.environ["DATABASE_URL"] == "sqlite:///from-env-file.db"


def test_real_environment_variable_wins_over_the_env_file(tmp_path, monkeypatch):
    import os

    import migrate_user_uid

    (tmp_path / "services").mkdir()
    (tmp_path / "services" / ".env").write_text("DATABASE_URL=sqlite:///from-env-file.db\n", encoding="utf-8")
    monkeypatch.setattr(migrate_user_uid, "REPO_ROOT", tmp_path)
    monkeypatch.setenv("DATABASE_URL", "sqlite://")
    migrate_user_uid.load_env_file()
    assert os.environ["DATABASE_URL"] == "sqlite://"


def test_missing_database_url_explains_where_to_put_it(tmp_path, monkeypatch, capsys):
    import migrate_user_uid

    monkeypatch.setattr(migrate_user_uid, "REPO_ROOT", tmp_path)  # sin services/.env
    monkeypatch.delenv("DATABASE_URL")
    assert migrate_user_uid.main([]) == 1
    assert "services/.env" in capsys.readouterr().err


def test_unknown_option_is_rejected(capsys):
    import migrate_user_uid

    assert migrate_user_uid.main(["--yes"]) == 1
    assert "--dry-run" in capsys.readouterr().err
