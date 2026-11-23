import mimetypes
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import PurePosixPath
from threading import RLock, Thread, Timer
from time import monotonic
from urllib.parse import unquote, urlsplit


@dataclass
class SandboxPreview:
    task_id: str
    server: ThreadingHTTPServer
    thread: Thread
    expiry_timer: Timer
    files: dict[str, str]
    root_prefix: str
    url: str
    created_at: float


class SandboxPreviewService:
    """Serve approved static project files from a loopback-only origin."""

    MAX_ACTIVE_PREVIEWS = 3
    PREVIEW_TTL_SECONDS = 2 * 60 * 60
    MAX_PREVIEW_BYTES = 50_000_000
    SECURITY_POLICY = (
        "default-src 'self'; script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
        "connect-src 'self'; form-action 'self'; base-uri 'self'; "
        "object-src 'none'; frame-ancestors 'none'"
    )

    def __init__(self):
        self._previews: dict[str, SandboxPreview] = {}
        self._lock = RLock()

    @staticmethod
    def _find_entry(files: dict[str, str]) -> str | None:
        if "index.html" in files:
            return "index.html"
        candidates = sorted(
            path
            for path in files
            if PurePosixPath(path).name == "index.html"
        )
        return candidates[0] if candidates else None

    def start(self, task_id: str, files: dict[str, str]) -> str | None:
        entry = self._find_entry(files)
        if entry is None:
            return None
        normalized_files = self._validate_files(files)
        root_prefix = PurePosixPath(entry).parent.as_posix()
        if root_prefix == ".":
            root_prefix = ""

        with self._lock:
            self._cleanup_expired()
            existing = self._previews.get(task_id)
            if existing is not None:
                return existing.url
            if len(self._previews) >= self.MAX_ACTIVE_PREVIEWS:
                raise RuntimeError(
                    "The maximum number of active local previews has been reached."
                )

            preview_ref: dict[str, SandboxPreview] = {}

            class PreviewHandler(BaseHTTPRequestHandler):
                def do_GET(self):
                    requested = unquote(urlsplit(self.path).path).lstrip("/")
                    if (
                        "\x00" in requested
                        or "\\" in requested
                        or any(part in {"..", "."} for part in requested.split("/"))
                    ):
                        self.send_error(404)
                        return
                    if not requested:
                        requested = "index.html"
                    if requested.endswith("/"):
                        requested += "index.html"
                    relative_path = (
                        f"{root_prefix}/{requested}"
                        if root_prefix
                        else requested
                    )
                    content = preview_ref["preview"].files.get(relative_path)
                    if (
                        content is None
                        and "." not in PurePosixPath(requested).name
                    ):
                        relative_path = (
                            f"{root_prefix}/index.html"
                            if root_prefix
                            else "index.html"
                        )
                        content = preview_ref["preview"].files.get(relative_path)
                    if content is None:
                        self.send_error(404)
                        return
                    body = content.encode("utf-8")
                    content_type = mimetypes.guess_type(relative_path)[0]
                    self.send_response(200)
                    self.send_header(
                        "Content-Type",
                        content_type or "application/octet-stream",
                    )
                    self.send_header("Content-Length", str(len(body)))
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("X-Content-Type-Options", "nosniff")
                    self.send_header(
                        "Content-Security-Policy",
                        SandboxPreviewService.SECURITY_POLICY,
                    )
                    self.end_headers()
                    self.wfile.write(body)

                def log_message(self, format, *args):
                    return

            server = ThreadingHTTPServer(("127.0.0.1", 0), PreviewHandler)
            server.daemon_threads = True
            port = int(server.server_address[1])
            url = f"http://127.0.0.1:{port}/"
            thread = Thread(
                target=server.serve_forever,
                name=f"agent-preview-{task_id[:12]}",
                daemon=True,
            )
            preview = SandboxPreview(
                task_id=task_id,
                server=server,
                thread=thread,
                expiry_timer=Timer(
                    self.PREVIEW_TTL_SECONDS,
                    self._expire,
                    args=(task_id, preview_ref),
                ),
                files=normalized_files,
                root_prefix=root_prefix,
                url=url,
                created_at=monotonic(),
            )
            preview_ref["preview"] = preview
            thread.start()
            self._previews[task_id] = preview
            preview.expiry_timer.daemon = True
            preview.expiry_timer.start()
            return url

    def stop(self, task_id: str) -> bool:
        with self._lock:
            preview = self._previews.pop(task_id, None)
            if preview is None:
                return False
            preview.expiry_timer.cancel()
            self._stop_preview(preview)
            return True

    def is_active(self, task_id: str) -> bool:
        with self._lock:
            return task_id in self._previews

    def _expire(
        self,
        task_id: str,
        preview_ref: dict[str, SandboxPreview],
    ) -> None:
        with self._lock:
            preview = preview_ref["preview"]
            if self._previews.get(task_id) is not preview:
                return
            self._previews.pop(task_id)
            self._stop_preview(preview)

    def _validate_files(self, files: dict[str, str]) -> dict[str, str]:
        total_bytes = 0
        normalized: dict[str, str] = {}
        for relative_path, content in files.items():
            path = PurePosixPath(relative_path)
            if (
                not relative_path
                or path.is_absolute()
                or "\\" in relative_path
                or "\x00" in relative_path
                or any(part in {"", ".", ".."} for part in path.parts)
            ):
                raise ValueError("Invalid local preview project file path.")
            if "\x00" in content:
                raise ValueError("Local preview files cannot contain binary content.")
            total_bytes += len(content.encode("utf-8"))
            if total_bytes > self.MAX_PREVIEW_BYTES:
                raise ValueError("Local preview exceeds the project size limit.")
            normalized[path.as_posix()] = content
        return normalized

    def _cleanup_expired(self) -> None:
        expired = [
            task_id
            for task_id, preview in self._previews.items()
            if monotonic() - preview.created_at >= self.PREVIEW_TTL_SECONDS
        ]
        for task_id in expired:
            preview = self._previews.pop(task_id)
            preview.expiry_timer.cancel()
            self._stop_preview(preview)

    @staticmethod
    def _stop_preview(preview: SandboxPreview) -> None:
        preview.server.shutdown()
        preview.server.server_close()
        if preview.thread.is_alive():
            preview.thread.join(timeout=2)
