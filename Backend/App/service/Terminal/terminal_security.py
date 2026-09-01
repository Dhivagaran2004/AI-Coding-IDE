import re


# =========================================
# Blocked Command Patterns
# =========================================

BLOCKED_PATTERNS = [

    # Windows shutdown / restart
    r"^\s*shutdown\b",
    r"^\s*restart-computer\b",

    # Disk formatting / destructive disk commands
    r"^\s*format\b",
    r"^\s*diskpart\b",

    # Windows registry modification
    r"^\s*reg\s+(add|delete|import|save)\b",

    # Service manipulation
    r"^\s*sc\s+(delete|stop|config)\b",

    # PowerShell encoded commands
    r"-encodedcommand\b",

    # Common destructive Unix commands
    r"^\s*mkfs\b",
    r"^\s*dd\s+.*of=/dev/",

]


# =========================================
# Dangerous Recursive Delete
# =========================================

DANGEROUS_DELETE_PATTERNS = [

    r"rm\s+-rf\s+/",
    r"rm\s+-fr\s+/",
    r"del\s+/s\s+/q\s+[a-zA-Z]:\\",
    r"rmdir\s+/s\s+/q\s+[a-zA-Z]:\\",

]


# =========================================
# Validate Command
# =========================================

def validate_terminal_command(
    command: str
) -> None:

    if not command or not command.strip():

        raise ValueError(
            "Command cannot be empty."
        )


    normalized_command = command.strip().lower()
import re


# =========================================
# Blocked Command Patterns
# =========================================

BLOCKED_PATTERNS = [

    # Windows shutdown / restart
    r"^\s*shutdown\b",
    r"^\s*restart-computer\b",

    # Disk formatting / destructive disk commands
    r"^\s*format\b",
    r"^\s*diskpart\b",

    # Windows registry modification
    r"^\s*reg\s+(add|delete|import|save)\b",

    # Service manipulation
    r"^\s*sc\s+(delete|stop|config)\b",

    # PowerShell encoded commands
    r"-encodedcommand\b",

    # Common destructive Unix commands
    r"^\s*mkfs\b",
    r"^\s*dd\s+.*of=/dev/",

]


# =========================================
# Dangerous Recursive Delete
# =========================================

DANGEROUS_DELETE_PATTERNS = [

    r"rm\s+-rf\s+/",
    r"rm\s+-fr\s+/",
    r"del\s+/s\s+/q\s+[a-zA-Z]:\\",
    r"rmdir\s+/s\s+/q\s+[a-zA-Z]:\\",

]


# =========================================
# Validate Command
# =========================================

def validate_terminal_command(
    command: str
) -> None:

    if not command or not command.strip():

        raise ValueError(
            "Command cannot be empty."
        )


    normalized_command = command.strip().lower()


    # =====================================
    # Block dangerous patterns
    # =====================================

    for pattern in BLOCKED_PATTERNS:

        if re.search(
            pattern,
            normalized_command,
            re.IGNORECASE
        ):

            raise ValueError(
                "This command is not allowed."
            )


    # =====================================
    # Block destructive recursive deletes
    # =====================================

    for pattern in DANGEROUS_DELETE_PATTERNS:

        if re.search(
            pattern,
            normalized_command,
            re.IGNORECASE
        ):

            raise ValueError(
                "Destructive recursive delete commands are not allowed."
            )

    # =====================================
    # Block dangerous patterns
    # =====================================

    for pattern in BLOCKED_PATTERNS:

        if re.search(
            pattern,
            normalized_command,
            re.IGNORECASE
        ):

            raise ValueError(
                "This command is not allowed."
            )


    # =====================================
    # Block destructive recursive deletes
    # =====================================

    for pattern in DANGEROUS_DELETE_PATTERNS:

        if re.search(
            pattern,
            normalized_command,
            re.IGNORECASE
        ):

            raise ValueError(
                "Destructive recursive delete commands are not allowed."
            )