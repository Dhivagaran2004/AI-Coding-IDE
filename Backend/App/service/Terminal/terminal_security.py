import shlex


SAFE_TERMINAL_COMMANDS = {
    "help",
    "pwd",
    "ls",
    "dir",
    "cat",
    "type",
    "head",
    "tail",
}
_SHELL_OPERATORS = frozenset("|&;<>\n\r`$")


def parse_terminal_command(command: str) -> list[str]:
    if not command or not command.strip():
        raise ValueError("Command cannot be empty.")
    if len(command) > 2000:
        raise ValueError("Command exceeds the 2000 character limit.")
    if any(operator in command for operator in _SHELL_OPERATORS):
        raise ValueError("Shell operators and command chaining are not allowed.")

    try:
        arguments = shlex.split(command, posix=True)
    except ValueError as error:
        raise ValueError("Command contains invalid quoting.") from error

    if not arguments:
        raise ValueError("Command cannot be empty.")
    if arguments[0].lower() not in SAFE_TERMINAL_COMMANDS:
        allowed = ", ".join(sorted(SAFE_TERMINAL_COMMANDS))
        raise ValueError(
            f"Command is not allowlisted. Supported commands: {allowed}."
        )
    return arguments


def validate_terminal_command(command: str) -> None:
    parse_terminal_command(command)
