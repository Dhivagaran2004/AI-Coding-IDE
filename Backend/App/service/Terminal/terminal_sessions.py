import asyncio
import os
import platform
import secrets
import shutil
import signal
import struct
import subprocess
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol
from starlette.websockets import WebSocket


MAX_SESSIONS_PER_USER = 8
SESSION_IDLE_SECONDS = 1800
TICKET_TTL_SECONDS = 30
MAX_OUTPUT_BUFFER_CHARS = 256_000
MAX_INPUT_CHARS = 16_000


class TerminalSessionError(RuntimeError):
    pass


class TerminalSessionUnavailable(TerminalSessionError):
    pass


class PtyAdapter(Protocol):
    def read(self, size: int) -> str: ...
    def write(self, data: str) -> None: ...
    def resize(self, columns: int, rows: int) -> None: ...
    def is_alive(self) -> bool: ...
    def terminate(self) -> None: ...


class UnixPtyAdapter:
    def __init__(
        self,
        command: list[str],
        cwd: Path,
        environment: dict[str, str],
        columns: int,
        rows: int,
    ) -> None:
        import pty

        self.master_fd, slave_fd = pty.openpty()
        self.resize(columns, rows, fd=slave_fd)
        try:
            self.process = subprocess.Popen(
                command,
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                cwd=str(cwd),
                env=environment,
                close_fds=True,
                start_new_session=True,
            )
        except OSError:
            os.close(self.master_fd)
            raise
        finally:
            os.close(slave_fd)

    def read(self, size: int) -> str:
        try:
            return os.read(self.master_fd, size).decode("utf-8", errors="replace")
        except OSError:
            return ""

    def write(self, data: str) -> None:
        remaining = memoryview(data.encode("utf-8"))
        while remaining:
            written = os.write(self.master_fd, remaining)
            if written <= 0:
                raise OSError("Terminal input could not be written.")
            remaining = remaining[written:]

    def resize(
        self,
        columns: int,
        rows: int,
        *,
        fd: int | None = None,
    ) -> None:
        import fcntl
        import termios

        target_fd = self.master_fd if fd is None else fd
        fcntl.ioctl(
            target_fd,
            termios.TIOCSWINSZ,
            struct.pack("HHHH", rows, columns, 0, 0),
        )

    def is_alive(self) -> bool:
        return self.process.poll() is None

    def terminate(self) -> None:
        if not self.is_alive():
            return
        try:
            os.killpg(self.process.pid, signal.SIGTERM)
            self.process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            os.killpg(self.process.pid, signal.SIGKILL)
            self.process.wait(timeout=2)
        finally:
            try:
                os.close(self.master_fd)
            except OSError:
                pass


class WindowsPtyAdapter:
    def __init__(
        self,
        command: list[str],
        cwd: Path,
        environment: dict[str, str],
        columns: int,
        rows: int,
    ) -> None:
        try:
            from winpty import PtyProcess
        except ImportError as error:
            raise TerminalSessionUnavailable(
                "Install the Windows pywinpty dependency to launch terminals."
            ) from error
        self.process = PtyProcess.spawn(
            command,
            cwd=str(cwd),
            env=environment,
            dimensions=(rows, columns),
        )

    def read(self, size: int) -> str:
        try:
            value = self.process.read(size)
        except (EOFError, OSError):
            return ""
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
        return value or ""

    def write(self, data: str) -> None:
        self.process.write(data)

    def resize(self, columns: int, rows: int) -> None:
        self.process.setwinsize(rows, columns)

    def is_alive(self) -> bool:
        return bool(self.process.isalive())

    def terminate(self) -> None:
        if self.is_alive():
            self.process.terminate(force=True)


@dataclass
class TerminalSession:
    id: str
    user_id: int
    project_id: int
    shell: str
    name: str
    cwd: str
    process: PtyAdapter
    created_at: float = field(default_factory=time.time)
    last_interaction_at: float = field(default_factory=time.monotonic)
    output_chunks: deque[str] = field(default_factory=deque)
    output_chars: int = 0
    output_queue: asyncio.Queue[str] | None = None
    websocket: WebSocket | None = None
    connection_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    reader_task: asyncio.Task[None] | None = None
    closed: bool = False

    def append_output(self, value: str) -> None:
        if not value:
            return
        self.output_chunks.append(value)
        self.output_chars += len(value)
        while self.output_chars > MAX_OUTPUT_BUFFER_CHARS and self.output_chunks:
            removed = self.output_chunks.popleft()
            self.output_chars -= len(removed)

    def output_snapshot(self) -> str:
        return "".join(self.output_chunks)

    def as_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "project_id": self.project_id,
            "shell": self.shell,
            "name": self.name,
            "cwd": self.cwd,
            "created_at": self.created_at,
            "running": self.process.is_alive(),
        }


class TerminalSessionManager:
    def __init__(
        self,
        *,
        idle_seconds: int = SESSION_IDLE_SECONDS,
        max_sessions_per_user: int = MAX_SESSIONS_PER_USER,
        platform_name: str | None = None,
    ) -> None:
        self.idle_seconds = idle_seconds
        self.max_sessions_per_user = max_sessions_per_user
        self.platform_name = platform_name or platform.system()
        self._sessions: dict[str, TerminalSession] = {}
        self._tickets: dict[str, tuple[str, int, int, float]] = {}
        self._pending_by_user: dict[int, int] = {}
        self._lock = threading.RLock()

    @staticmethod
    def _resolve_shell(shell: str, platform_name: str) -> list[str]:
        normalized = shell.strip().lower()
        if normalized == "powershell":
            executable = shutil.which("pwsh") or shutil.which("powershell.exe")
            if executable is None:
                raise TerminalSessionUnavailable(
                    "PowerShell is not available on this host."
                )
            return [executable, "-NoLogo", "-NoExit"]
        if normalized == "cmd":
            if platform_name != "Windows":
                raise TerminalSessionError("CMD is only available on Windows.")
            executable = os.environ.get("COMSPEC") or shutil.which("cmd.exe")
            if executable is None:
                raise TerminalSessionUnavailable(
                    "CMD is not available on this host."
                )
            return [executable, "/Q"]
        if normalized == "bash":
            executable = shutil.which("bash")
            if executable is None:
                raise TerminalSessionUnavailable(
                    "Bash is not available on this host."
                )
            return [executable, "--login", "-i"]
        raise TerminalSessionError("Unsupported terminal shell.")

    def _new_process(
        self,
        shell: str,
        cwd: Path,
        columns: int,
        rows: int,
    ) -> PtyAdapter:
        command = self._resolve_shell(shell, self.platform_name)
        environment = os.environ.copy()
        environment["TERM"] = "xterm-256color"
        adapter = WindowsPtyAdapter if self.platform_name == "Windows" else UnixPtyAdapter
        return adapter(command, cwd, environment, columns, rows)

    async def create_session(
        self,
        *,
        user_id: int,
        project_id: int,
        cwd: Path,
        shell: str,
        name: str | None = None,
        columns: int = 80,
        rows: int = 24,
    ) -> TerminalSession:
        self._cleanup_tickets()
        with self._lock:
            owned_count = sum(
                session.user_id == user_id and not session.closed
                for session in self._sessions.values()
            ) + self._pending_by_user.get(user_id, 0)
            if owned_count >= self.max_sessions_per_user:
                raise TerminalSessionError(
                    "The maximum number of active terminal sessions was reached."
                )
            self._pending_by_user[user_id] = (
                self._pending_by_user.get(user_id, 0) + 1
            )

        registered = False
        try:
            process_task = asyncio.create_task(
                asyncio.to_thread(
                    self._new_process,
                    shell,
                    cwd,
                    columns,
                    rows,
                )
            )
            try:
                process = await asyncio.shield(process_task)
            except asyncio.CancelledError:
                process = await process_task
                await asyncio.to_thread(process.terminate)
                raise
            session = TerminalSession(
                id=uuid.uuid4().hex,
                user_id=user_id,
                project_id=project_id,
                shell=shell,
                name=(name or shell.title()).strip()[:80] or shell.title(),
                cwd=str(cwd),
                process=process,
            )
            with self._lock:
                self._sessions[session.id] = session
                self._decrement_pending(user_id)
                registered = True
        finally:
            if not registered:
                with self._lock:
                    self._decrement_pending(user_id)
        session.reader_task = asyncio.create_task(self._read_output(session))
        return session

    def issue_ticket(
        self,
        session_id: str,
        user_id: int,
        project_id: int,
    ) -> str:
        self._cleanup_tickets()
        with self._lock:
            session = self._sessions.get(session_id)
            if (
                session is None
                or session.closed
                or session.user_id != user_id
                or session.project_id != project_id
            ):
                raise TerminalSessionError("Terminal session not found.")
            ticket = secrets.token_urlsafe(32)
            self._tickets[ticket] = (
                session_id,
                user_id,
                project_id,
                time.monotonic() + TICKET_TTL_SECONDS,
            )
            return ticket

    def consume_ticket(
        self,
        ticket: str,
        session_id: str,
        project_id: int,
    ) -> TerminalSession | None:
        with self._lock:
            issued = self._tickets.pop(ticket, None)
            if issued is None:
                return None
            issued_session_id, user_id, issued_project_id, expires_at = issued
            session = self._sessions.get(issued_session_id)
            if (
                expires_at < time.monotonic()
                or issued_session_id != session_id
                or issued_project_id != project_id
                or session is None
                or session.closed
                or session.user_id != user_id
            ):
                return None
            session.last_interaction_at = time.monotonic()
            return session

    def list_sessions(
        self,
        user_id: int,
        project_id: int,
    ) -> list[dict[str, object]]:
        with self._lock:
            sessions = [
                session.as_dict()
                for session in self._sessions.values()
                if session.user_id == user_id
                and session.project_id == project_id
                and not session.closed
            ]
        return sorted(sessions, key=lambda session: float(session["created_at"]))

    def rename_session(
        self,
        session_id: str,
        user_id: int,
        project_id: int,
        name: str,
    ) -> dict[str, object]:
        normalized_name = name.strip()
        if not normalized_name or len(normalized_name) > 80:
            raise TerminalSessionError(
                "Terminal names must contain 1 to 80 characters."
            )
        with self._lock:
            session = self._get_owned_session(session_id, user_id, project_id)
            session.name = normalized_name
            session.last_interaction_at = time.monotonic()
            return session.as_dict()

    async def attach(self, session: TerminalSession, websocket: WebSocket):
        async with session.connection_lock:
            previous_websocket = session.websocket
            queue: asyncio.Queue[str] = asyncio.Queue(maxsize=256)
            session.websocket = websocket
            session.output_queue = queue
            session.last_interaction_at = time.monotonic()
            backlog = session.output_snapshot()
        if previous_websocket is not None and previous_websocket is not websocket:
            await previous_websocket.close(code=4001)
        return queue, backlog

    async def detach(
        self,
        session: TerminalSession,
        websocket: WebSocket,
        queue: asyncio.Queue[str],
    ) -> None:
        async with session.connection_lock:
            if session.websocket is websocket:
                session.websocket = None
                session.output_queue = None

    async def send_input(self, session: TerminalSession, value: str) -> None:
        if len(value) > MAX_INPUT_CHARS:
            raise TerminalSessionError("Terminal input exceeds the size limit.")
        if session.closed:
            raise TerminalSessionError("Terminal session is closed.")
        session.last_interaction_at = time.monotonic()
        try:
            await asyncio.to_thread(session.process.write, value)
        except OSError as error:
            raise TerminalSessionError(
                "The terminal process is no longer accepting input."
            ) from error

    async def resize(
        self,
        session: TerminalSession,
        columns: int,
        rows: int,
    ) -> None:
        if not 1 <= columns <= 400 or not 1 <= rows <= 200:
            raise TerminalSessionError("Terminal dimensions are outside the allowed range.")
        session.last_interaction_at = time.monotonic()
        await asyncio.to_thread(session.process.resize, columns, rows)

    async def close_session(
        self,
        session_id: str,
        user_id: int,
        project_id: int,
    ) -> None:
        with self._lock:
            session = self._get_owned_session(session_id, user_id, project_id)
            session.closed = True
            self._sessions.pop(session_id, None)
            self._remove_session_tickets(session_id)
        await self._terminate(session)

    async def close_all(self) -> None:
        with self._lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()
            self._tickets.clear()
            for session in sessions:
                session.closed = True
        await asyncio.gather(
            *(self._terminate(session) for session in sessions),
            return_exceptions=False,
        )

    async def cleanup_expired(self) -> None:
        cutoff = time.monotonic() - self.idle_seconds
        with self._lock:
            expired = [
                session
                for session in self._sessions.values()
                if session.last_interaction_at < cutoff
                and session.websocket is None
            ]
            for session in expired:
                session.closed = True
                self._sessions.pop(session.id, None)
                self._remove_session_tickets(session.id)
        await asyncio.gather(
            *(self._terminate(session) for session in expired),
            return_exceptions=False,
        )
        self._cleanup_tickets()

    async def cleanup_loop(self) -> None:
        while True:
            await asyncio.sleep(30)
            await self.cleanup_expired()

    async def _read_output(self, session: TerminalSession) -> None:
        while not session.closed:
            value = await asyncio.to_thread(session.process.read, 4096)
            if value:
                session.append_output(value)
                queue = session.output_queue
                if queue is not None:
                    if queue.full():
                        try:
                            queue.get_nowait()
                        except asyncio.QueueEmpty:
                            pass
                    try:
                        queue.put_nowait(value)
                    except asyncio.QueueFull:
                        pass
            elif not session.process.is_alive():
                break
            else:
                await asyncio.sleep(0.02)
        if (
            not session.closed
            and not session.process.is_alive()
            and session.websocket is not None
        ):
            try:
                await session.websocket.close(code=1000)
            except RuntimeError:
                pass

    async def _terminate(self, session: TerminalSession) -> None:
        if session.websocket is not None:
            try:
                await session.websocket.close(code=1001)
            except RuntimeError:
                pass
        await asyncio.to_thread(session.process.terminate)
        if session.reader_task is not None and session.reader_task is not asyncio.current_task():
            session.reader_task.cancel()
            await asyncio.gather(session.reader_task, return_exceptions=True)

    def _get_owned_session(
        self,
        session_id: str,
        user_id: int,
        project_id: int,
    ) -> TerminalSession:
        session = self._sessions.get(session_id)
        if (
            session is None
            or session.closed
            or session.user_id != user_id
            or session.project_id != project_id
        ):
            raise TerminalSessionError("Terminal session not found.")
        return session

    def _remove_session_tickets(self, session_id: str) -> None:
        for ticket, (ticket_session_id, *_rest) in list(self._tickets.items()):
            if ticket_session_id == session_id:
                self._tickets.pop(ticket, None)

    def _cleanup_tickets(self) -> None:
        now = time.monotonic()
        with self._lock:
            for ticket, (_, _, _, expires_at) in list(self._tickets.items()):
                if expires_at <= now:
                    self._tickets.pop(ticket, None)

    def _decrement_pending(self, user_id: int) -> None:
        pending = self._pending_by_user.get(user_id, 1) - 1
        if pending:
            self._pending_by_user[user_id] = pending
        else:
            self._pending_by_user.pop(user_id, None)
