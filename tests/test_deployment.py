import sqlite3
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from deploy.backup import backup
from scripts.build_pages import build
from tests.test_remote import config, Upstream


def test_pages_allowlist_and_no_overwrite(tmp_path):
    build("https://api.example.com", tmp_path / "pages")
    root = tmp_path / "pages"
    files = {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}
    assert files == {"index.html", "static/app.js", "static/style.css", "static/config.js", ".nojekyll"}
    html = (root / "index.html").read_text(encoding="utf-8")
    assert '{{' not in html and '{%' not in html
    assert 'src="static/app.js' in html and 'href="static/style.css' in html
    assert "connect-src https://api.example.com" in html
    assert "script-src 'self'" in html
    with pytest.raises(ValueError, match="empty output"):
        build("https://api.example.com", root)


@pytest.mark.parametrize("origin", ["http://api.example.com", "https://user:password@example.com", "https://example.com/path",
    "https://example.com?token=secret", "https://example.com/#fragment", "https://example.com\"; evil"])
def test_pages_rejects_unsafe_api_origin(tmp_path, origin):
    with pytest.raises(ValueError):
        build(origin, tmp_path / "pages")
    assert not (tmp_path / "pages").exists()


def test_sqlite_wal_backup_and_no_overwrite(tmp_path):
    source, dest = tmp_path / "source.sqlite3", tmp_path / "backup.sqlite3"
    with sqlite3.connect(source) as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("CREATE TABLE tasks (id TEXT)")
        connection.execute("INSERT INTO tasks VALUES ('checkpoint')")
        connection.commit()
        backup(source, dest)
        with sqlite3.connect(dest) as restored:
            assert restored.execute("SELECT id FROM tasks").fetchall() == [("checkpoint",)]
    with pytest.raises(FileExistsError):
        backup(source, dest)


def test_metrics_private_and_credential_free(tmp_path):
    app = create_app(config(tmp_path), httpx.MockTransport(Upstream()), start_worker=False)
    with TestClient(app) as client:
        assert client.get("/internal/metrics").status_code == 404
        # Forwarded headers from arbitrary callers do not bypass address checks.
        assert client.get("/internal/metrics", headers={"X-Forwarded-For": "127.0.0.1"}).status_code == 404
    app = create_app(config(tmp_path, metrics_allowed_ips=["testclient"]), httpx.MockTransport(Upstream()), start_worker=False)
    with TestClient(app) as client:
        client.get("/api/tasks")
        metrics = client.get("/internal/metrics")
        assert metrics.status_code == 200
        assert metrics.json()["http_requests_5m"] == 1
        assert metrics.json()["database_bytes"] > 0
        assert "credential" not in metrics.text and "token" not in metrics.text
