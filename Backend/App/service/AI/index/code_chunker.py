from dataclasses import dataclass
from pathlib import PurePosixPath
import re


@dataclass
class CodeChunk:
    project_id: int
    file_id: int
    file_path: str
    language: str | None
    start_line: int
    end_line: int
    content: str


class CodeChunker:
    SECRET_CONTENT_PATTERNS = (
        re.compile(
            r"(?i)\b(?:api[_-]?key|secret(?:[_-]?key)?|password|passwd|"
            r"access[_-]?token|refresh[_-]?token|credential|authorization|"
            r"database_url)\b\s*[:=]\s*[\"']?[A-Za-z0-9_./+=:@-]{12,}"
        ),
        re.compile(
            r"\b(?:hf_[A-Za-z0-9]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|"
            r"github_pat_[A-Za-z0-9_]{20,}|AKIA[0-9A-Z]{16})\b"
        ),
        re.compile(
            r"\b[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{12,}\."
            r"[A-Za-z0-9_-]{12,}\b"
        ),
        re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    )
    IGNORED_DIRECTORIES = {
        ".git",
        ".venv",
        "venv",
        "node_modules",
        "dist",
        "build",
        "coverage",
        "__pycache__",
        "target",
        "vendor",
        ".next",
        ".turbo",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        "out",
        "bin",
        "obj",
        "pods",
        "deriveddata",
    }
    IGNORED_EXTENSIONS = {
        ".bin", ".bmp", ".class", ".dll", ".exe", ".gif",
        ".gz", ".ico", ".jpeg", ".jpg", ".lock", ".pdf",
        ".png", ".pyc", ".so", ".tar", ".webp", ".zip",
        ".pem", ".key", ".p12", ".pfx", ".crt", ".cer",
        ".db", ".sqlite", ".sqlite3", ".mp3", ".mp4", ".mov",
        ".avi", ".ttf", ".otf", ".woff", ".woff2", ".jar",
        ".war", ".wasm", ".map", ".dylib", ".app",
    }
    SECRET_NAMES = {
        "id_rsa",
        "id_ed25519",
        "credentials",
        "credentials.json",
        "secrets.json",
    }
    IGNORED_FILE_NAMES = {
        "package-lock.json",
        "yarn.lock",
        "pnpm-lock.yaml",
        "poetry.lock",
        "uv.lock",
        "cargo.lock",
        "composer.lock",
        "gemfile.lock",
        "podfile.lock",
    }

    def __init__(
        self,
        max_lines: int = 100,
        overlap_lines: int = 12,
        max_chars: int = 6000,
    ):
        if max_lines < 1 or overlap_lines < 0 or max_chars < 1:
            raise ValueError("Chunk limits must be positive.")
        self.max_lines = max_lines
        self.overlap_lines = min(overlap_lines, max_lines - 1)
        self.max_chars = max_chars

    @classmethod
    def is_indexable_path(cls, file_path: str) -> bool:
        path = PurePosixPath(file_path.replace("\\", "/"))
        lowered_parts = [part.lower() for part in path.parts]
        name = path.name.lower()

        if any(part in cls.IGNORED_DIRECTORIES for part in lowered_parts):
            return False
        if name == ".env" or name.startswith(".env."):
            return False
        if name in cls.IGNORED_FILE_NAMES:
            return False
        if name in cls.SECRET_NAMES or any(
            marker in name for marker in ("secret", "credential")
        ):
            return False
        if path.suffix.lower() in cls.IGNORED_EXTENSIONS:
            return False
        return True

    @classmethod
    def contains_sensitive_content(cls, content: str | None) -> bool:
        if not content:
            return False
        return any(
            pattern.search(content)
            for pattern in cls.SECRET_CONTENT_PATTERNS
        )

    def chunk_file(
        self,
        project_id: int,
        file_id: int,
        file_path: str,
        language: str | None,
        content: str | None,
    ) -> list[CodeChunk]:
        if (
            not self.is_indexable_path(file_path)
            or             not content
            or "\x00" in content
            or self.contains_sensitive_content(content)
        ):
            return []

        lines = content.splitlines()
        chunks: list[CodeChunk] = []
        start = 0

        while start < len(lines):
            if len(lines[start]) > self.max_chars:
                line = lines[start]
                for offset in range(0, len(line), self.max_chars):
                    chunks.append(
                        CodeChunk(
                            project_id=project_id,
                            file_id=file_id,
                            file_path=file_path,
                            language=language,
                            start_line=start + 1,
                            end_line=start + 1,
                            content=line[offset:offset + self.max_chars],
                        )
                    )
                start += 1
                continue

            end = start
            character_count = 0
            while end < len(lines) and end - start < self.max_lines:
                next_size = len(lines[end]) + (1 if end > start else 0)
                if character_count + next_size > self.max_chars:
                    break
                character_count += next_size
                end += 1

            if end == start:
                end += 1

            chunks.append(
                CodeChunk(
                    project_id=project_id,
                    file_id=file_id,
                    file_path=file_path,
                    language=language,
                    start_line=start + 1,
                    end_line=end,
                    content="\n".join(lines[start:end]),
                )
            )

            if end >= len(lines):
                break
            start = max(start + 1, end - self.overlap_lines)

        return chunks