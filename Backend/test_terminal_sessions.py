import asyncio
import os
import platform
import shutil
import time
from pathlib import Path

import pytest
from starlette.websockets import WebSocketState

from App.service.Terminal.terminal_sessions import (
    TerminalSessionError,
    TerminalSessionManager,
)


class FakePty:
    def __init__(self):
        self.alive = True
        self.input = []
        self.size = None

    def read(self, _size):
        return ""

    def write(self, data):
        self.input.append(data)

    def resize(self, columns, rows):
        self.size = (columns, rows)

    def is_alive(self):
        return self.alive

    def terminate(self):
        self.alive = False


def manager_with_fake_pty(*, idle_seconds=1800, max_sessions_per_user=8):
    manager = TerminalSessionManager(
        idle_seconds=idle_seconds,
        max_sessions_per_user=max_sessions_per_user,
        platform_name="Windows",
    )
    processes = []

    def spawn(_shell, _cwd, _columns, _rows):
        process = FakePty()
        processes.append(process)
        return process

    manager._new_process = spawn
    return manager, processes


def test_sessions_are_owned_scoped_and_reconnect_tickets_are_one_time(tmp_path):
    async def run():
        manager, _processes = manager_with_fake_pty()
        session = await manager.create_session(
            user_id=1,
            project_id=9,
            cwd=tmp_path,
            shell="powershell",
        )
        ticket = manager.issue_ticket(session.id, user_id=1, project_id=9)

        assert manager.consume_ticket(ticket, session.id, 9) is session
        assert manager.consume_ticket(ticket, session.id, 9) is None
        assert manager.list_sessions(user_id=2, project_id=9) == []

        with pytest.raises(TerminalSessionError, match="not found"):
            manager.issue_ticket(session.id, user_id=2, project_id=9)

        await manager.close_all()

    asyncio.run(run())


def test_session_input_resize_and_close(tmp_path):
    async def run():
        manager, processes = manager_with_fake_pty()
        session = await manager.create_session(
            user_id=1,
            project_id=9,
            cwd=tmp_path,
            shell="cmd",
        )

        await manager.send_input(session, "cd project\r")
        await manager.resize(session, 120, 40)

        assert processes[0].input == ["cd project\r"]
        assert processes[0].size == (120, 40)
        await manager.close_session(session.id, user_id=1, project_id=9)
        assert processes[0].alive is False
        assert manager.list_sessions(user_id=1, project_id=9) == []

    asyncio.run(run())


def test_multiple_sessions_survive_websocket_disconnect_and_reconnect(tmp_path):
    class FakeWebSocket:
        def __init__(self):
            self.client_state = WebSocketState.CONNECTED
            self.application_state = WebSocketState.CONNECTED
            self.close_codes = []
            self.fail_close = False

        async def close(self, code=1000):
            if (
                self.client_state is WebSocketState.DISCONNECTED
                or self.application_state is WebSocketState.DISCONNECTED
            ):
                self.application_state = WebSocketState.DISCONNECTED
                raise RuntimeError("Cannot call close after disconnect.")
            self.application_state = WebSocketState.DISCONNECTED
            self.close_codes.append(code)
            if self.fail_close:
                raise RuntimeError("The connection closed during close.")

    async def run():
        manager, processes = manager_with_fake_pty()
        first = await manager.create_session(
            user_id=1,
            project_id=9,
            cwd=tmp_path,
            shell="powershell",
        )
        second = await manager.create_session(
            user_id=1,
            project_id=9,
            cwd=tmp_path,
            shell="cmd",
        )
        first.append_output("retained output")
        original_socket = FakeWebSocket()
        queue, backlog = await manager.attach(first, original_socket)

        assert backlog == "retained output"
        await manager.detach(first, original_socket, queue)
        assert processes[0].alive is True

        reconnect_ticket = manager.issue_ticket(
            first.id,
            user_id=1,
            project_id=9,
        )
        reconnected = manager.consume_ticket(
            reconnect_ticket,
            first.id,
            project_id=9,
        )
        assert reconnected is first
        replacement_socket = FakeWebSocket()
        _queue, replacement_backlog = await manager.attach(
            reconnected,
            replacement_socket,
        )

        assert replacement_backlog == "retained output"

        active_replacement = FakeWebSocket()
        await manager.attach(reconnected, active_replacement)
        assert replacement_socket.close_codes == [4001]

        active_replacement.client_state = WebSocketState.DISCONNECTED
        final_socket = FakeWebSocket()
        await manager.attach(reconnected, final_socket)
        assert active_replacement.close_codes == []

        final_socket.fail_close = True
        last_socket = FakeWebSocket()
        await manager.attach(reconnected, last_socket)
        assert final_socket.close_codes == [4001]

        assert len(manager.list_sessions(user_id=1, project_id=9)) == 2
        await manager.close_all()
        assert processes[0].alive is False
        assert processes[1].alive is False

    asyncio.run(run())


def test_session_limit_and_idle_cleanup(tmp_path):
    async def run():
        manager, processes = manager_with_fake_pty(
            idle_seconds=1,
            max_sessions_per_user=1,
        )
        session = await manager.create_session(
            user_id=1,
            project_id=9,
            cwd=Path(tmp_path),
            shell="powershell",
        )

        with pytest.raises(TerminalSessionError, match="maximum number"):
            await manager.create_session(
                user_id=1,
                project_id=10,
                cwd=Path(tmp_path),
                shell="powershell",
            )

        session.last_interaction_at = time.monotonic() - 2
        await manager.cleanup_expired()

        assert processes[0].alive is False
        assert manager.list_sessions(user_id=1, project_id=9) == []

    asyncio.run(run())


@pytest.mark.skipif(
    platform.system() != "Windows"
    or not (shutil.which("powershell.exe") or shutil.which("pwsh"))
    or not (shutil.which("cmd.exe") or os.environ.get("COMSPEC")),
    reason="PowerShell and CMD PTY integration requires Windows shells.",
)
@pytest.mark.parametrize("shell", ["powershell", "cmd"])
def test_real_windows_shell_preserves_environment_and_working_directory(
    shell,
    tmp_path,
):
    async def run():
        manager = TerminalSessionManager()
        session = await manager.create_session(
            user_id=1,
            project_id=9,
            cwd=tmp_path,
            shell=shell,
        )
        try:
            working_directory = str(tmp_path)
            if shell == "powershell":
                escaped_directory = working_directory.replace("'", "''")
                setup = (
                    f"Set-Location '{escaped_directory}'; "
                    "$env:IDE_TERMINAL_TEST='persisted'; "
                    "Write-Output TERMINAL_SETUP_DONE"
                )
                verify = (
                    "Write-Output $env:IDE_TERMINAL_TEST; "
                    "Get-Location"
                )
            else:
                setup = (
                    f'cd /d "{working_directory}" & '
                    "set IDE_TERMINAL_TEST=persisted & "
                    "echo TERMINAL_SETUP_DONE"
                )
                verify = "echo %IDE_TERMINAL_TEST% & cd"
            await manager.send_input(session, setup + "\r\n")
            await manager.send_input(session, verify + "\r\n")

            for _ in range(120):
                output = session.output_snapshot()
                if "persisted" in output and working_directory in output:
                    break
                await asyncio.sleep(0.05)

            assert "persisted" in session.output_snapshot()
            assert working_directory in session.output_snapshot()
        finally:
            await manager.close_session(
                session.id,
                user_id=1,
                project_id=9,
            )

    asyncio.run(run())
