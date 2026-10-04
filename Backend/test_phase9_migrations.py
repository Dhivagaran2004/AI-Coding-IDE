import os
import sqlite3
import subprocess
import sys
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parent


def _migration_environment(database_path: Path) -> dict[str, str]:
    environment = os.environ.copy()
    environment.update(
        {
            "DATABASE_URL": f"sqlite:///{database_path.as_posix()}",
            "DB_SCHEMA": "",
            "SECRET_KEY": "migration-test-only",
            "ALGORITHM": "HS256",
            "ACCESS_TOKEN_EXPIRE_MINUTES": "30",
        }
    )
    return environment


def _run_alembic(database_path: Path) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "alembic",
            "-c",
            "alembic.ini",
            "upgrade",
            "head",
        ],
        cwd=BACKEND_DIR,
        env=_migration_environment(database_path),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_migrations_upgrade_fresh_database(tmp_path):
    database_path = tmp_path / "fresh.sqlite"

    _run_alembic(database_path)

    with sqlite3.connect(database_path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert {
            "users",
            "projects",
            "project_files",
            "repository_indexes",
            "repository_chunks",
            "ai_conversations",
            "ai_conversation_messages",
            "agent_task_records",
            "ai_usage",
            "repository_index_jobs",
        } <= tables
        assert connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone()[0] == "20261004_04"
        assert "error_message" in {
            row[1]
            for row in connection.execute("PRAGMA table_info(repository_indexes)")
        }
        assert "ix_repository_index_jobs_project_id" in {
            row[1]
            for row in connection.execute(
                "PRAGMA index_list(repository_index_jobs)"
            )
        }
        assert connection.execute(
            "PRAGMA foreign_key_list(repository_index_jobs)"
        ).fetchone()[2] == "projects"


def test_migrations_preserve_existing_application_data(tmp_path):
    database_path = tmp_path / "existing.sqlite"
    environment = _migration_environment(database_path)
    create_schema = (
        "import App.models; "
        "from App.database.database import Base, engine; "
        "Base.metadata.create_all(bind=engine)"
    )
    setup_result = subprocess.run(
        [sys.executable, "-c", create_schema],
        cwd=BACKEND_DIR,
        env=environment,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert setup_result.returncode == 0, setup_result.stdout + setup_result.stderr

    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "INSERT INTO users (id, name, email, password_hash) "
            "VALUES (7, 'Existing User', 'existing@example.test', 'hashed')"
        )
        connection.execute(
            "INSERT INTO projects (id, user_id, name, language) "
            "VALUES (11, 7, 'Existing Project', 'python')"
        )
        connection.execute(
            "INSERT INTO ai_conversations "
            "(id, project_id, user_id, title, created_at, updated_at) "
            "VALUES (13, 11, 7, 'Existing conversation', "
            "'2026-10-01 00:00:00', '2026-10-01 00:00:00')"
        )

    _run_alembic(database_path)

    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT email FROM users WHERE id = 7"
        ).fetchone()[0] == "existing@example.test"
        assert connection.execute(
            "SELECT name FROM projects WHERE id = 11"
        ).fetchone()[0] == "Existing Project"
        assert connection.execute(
            "SELECT title FROM ai_conversations WHERE id = 13"
        ).fetchone()[0] == "Existing conversation"
        assert connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone()[0] == "20261004_04"
