import time
from urllib.error import HTTPError
from urllib.request import urlopen

import pytest

from App.service.sandbox_preview_service import SandboxPreviewService


def test_preview_serves_approved_static_snapshot_on_loopback_and_stops():
    preview = SandboxPreviewService()
    url = preview.start(
        "task-static",
        {
            "frontend/index.html": "<h1>Preview</h1><script src='app.js'></script>",
            "frontend/app.js": "document.title = 'Preview';",
        },
    )
    assert url is not None
    assert preview._previews["task-static"].server.server_address[0] == "127.0.0.1"
    try:
        with urlopen(url, timeout=2) as response:
            page = response.read().decode("utf-8")
            assert response.status == 200
            assert "Preview" in page
            assert response.headers["Content-Security-Policy"]
        with urlopen(f"{url}app.js", timeout=2) as response:
            assert response.read().decode("utf-8") == "document.title = 'Preview';"
        with urlopen(f"{url}users/profile", timeout=2) as response:
            assert response.read().decode("utf-8") == page
    finally:
        assert preview.stop("task-static")
    assert not preview.stop("task-static")


def test_preview_rejects_traversal_and_projects_without_static_entry():
    preview = SandboxPreviewService()
    assert preview.start("task-no-ui", {"main.py": "print('hello')\n"}) is None

    url = preview.start("task-traversal", {
        "index.html": "<h1>Preview</h1>",
    })
    assert url is not None
    try:
        with pytest.raises(HTTPError) as response:
            urlopen(f"{url}%2e%2e/README.md", timeout=2)
        assert response.value.code == 404
    finally:
        preview.stop("task-traversal")


def test_preview_stops_automatically_after_ttl():
    preview = SandboxPreviewService()
    preview.PREVIEW_TTL_SECONDS = 0.02
    url = preview.start("task-expiry", {"index.html": "<h1>Preview</h1>"})
    assert url is not None
    preview_thread = preview._previews["task-expiry"].thread

    time.sleep(0.1)

    assert not preview.is_active("task-expiry")
    assert not preview_thread.is_alive()
