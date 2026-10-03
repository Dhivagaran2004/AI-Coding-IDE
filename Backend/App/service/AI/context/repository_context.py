from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from App.models.project_file import ProjectFile


@dataclass
class RepositoryContext:
    """
    Repository context prepared for the AI model.
    """

    project_id: int
    content: str
    file_count: int
    character_count: int


class RepositoryContextBuilder:
    """
    Builds safe, structured repository context
    from the project_files database table.
    """

    DEFAULT_MAX_CHARS = 50000

    IGNORED_FILE_NAMES = {
        ".env",
        ".env.local",
        ".env.production",
        ".env.development",
        "id_rsa",
        "id_rsa.pub",
    }

    IGNORED_EXTENSIONS = {
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".webp",
        ".ico",
        ".bmp",
        ".pdf",
        ".zip",
        ".tar",
        ".gz",
        ".7z",
        ".exe",
        ".dll",
        ".so",
        ".bin",
        ".pyc",
        ".class",
        ".o",
        ".obj",
        ".lock",
    }

    def __init__(
        self,
        db: Session,
        project_id: int,
        max_chars: int | None = None,
    ):
        self.db = db
        self.project_id = project_id

        self.max_chars = (
            max_chars
            if max_chars is not None
            else self.DEFAULT_MAX_CHARS
        )

        if self.max_chars <= 0:
            raise ValueError(
                "max_chars must be greater than zero."
            )

    # =========================================================
    # PUBLIC API
    # =========================================================

    def build(self) -> RepositoryContext:
        """
        Build repository context for the project.
        """

        files = self._load_project_files()

        file_sections: list[str] = []

        for project_file in files:
            if not self._should_include_file(
                project_file
            ):
                continue

            section = self._format_file(
                project_file
            )

            if section:
                file_sections.append(section)

        repository_tree = (
            self._build_repository_tree(files)
        )

        context = self._combine_context(
            repository_tree=repository_tree,
            file_sections=file_sections,
        )

        context = self._limit_context(context)

        return RepositoryContext(
            project_id=self.project_id,
            content=context,
            file_count=len(file_sections),
            character_count=len(context),
        )

    def build_tree(self) -> str:
        """Build only the safe project structure, without file contents."""

        return self._limit_context(
            self._build_repository_tree(
                self._load_project_files(),
                include_empty_files=True,
            )
        )

    # =========================================================
    # LOAD PROJECT FILES
    # =========================================================

    def _load_project_files(
        self,
    ) -> list[ProjectFile]:
        """
        Load all files and folders belonging to
        the requested project.
        """

        return (
            self.db.query(ProjectFile)
            .filter(
                ProjectFile.project_id
                == self.project_id
            )
            .order_by(
                ProjectFile.parent_id.asc(),
                ProjectFile.name.asc(),
            )
            .all()
        )

    # =========================================================
    # FILE FILTERING
    # =========================================================

    def _should_include_file(
        self,
        project_file: ProjectFile,
    ) -> bool:
        """
        Determine whether a file is safe and useful
        for AI context.
        """

        if project_file.type != "file":
            return False

        file_name = (
            project_file.name or ""
        ).strip()

        if not file_name:
            return False

        lower_name = file_name.lower()

        ignored_names = {
            name.lower()
            for name in self.IGNORED_FILE_NAMES
        }

        if lower_name in ignored_names:
            return False

        for extension in self.IGNORED_EXTENSIONS:
            if lower_name.endswith(extension):
                return False

        if not project_file.content:
            return False

        return True

    # =========================================================
    # FILE FORMATTING
    # =========================================================

    def _format_file(
        self,
        project_file: ProjectFile,
    ) -> str:
        """
        Convert a project file into an AI-readable
        context section.
        """

        path = self._get_file_path(
            project_file
        )

        content = project_file.content or ""

        language = (
            project_file.language
            or self._detect_language(
                project_file.name
            )
        )

        if language:
            return (
                f"--- FILE: {path} ---\n"
                f"LANGUAGE: {language}\n"
                f"\n"
                f"```{language}\n"
                f"{content}\n"
                f"```\n"
            )

        return (
            f"--- FILE: {path} ---\n"
            f"\n"
            f"```\n"
            f"{content}\n"
            f"```\n"
        )

    # =========================================================
    # FILE PATH
    # =========================================================

    def _get_file_path(
        self,
        project_file: ProjectFile,
    ) -> str:
        """
        Build the complete project path.

        Example:

            app/router/auth.py
        """

        parts: list[str] = []

        current = project_file

        visited_ids: set[int] = set()

        while current is not None:
            current_id = getattr(
                current,
                "id",
                None,
            )

            if (
                current_id is not None
                and current_id in visited_ids
            ):
                break

            if current_id is not None:
                visited_ids.add(current_id)

            name = (
                current.name or ""
            ).strip()

            if name:
                parts.append(name)

            parent_id = getattr(
                current,
                "parent_id",
                None,
            )

            if parent_id is None:
                break

            current = self._find_file_by_id(
                parent_id
            )

        parts.reverse()

        return "/".join(parts)

    # =========================================================
    # FIND FILE BY ID
    # =========================================================

    def _find_file_by_id(
        self,
        file_id: int,
    ) -> ProjectFile | None:
        """
        Find a file or folder inside the current project.
        """

        return (
            self.db.query(ProjectFile)
            .filter(
                ProjectFile.id == file_id,
                ProjectFile.project_id
                == self.project_id,
            )
            .first()
        )

    # =========================================================
    # REPOSITORY TREE
    # =========================================================

    def _build_repository_tree(
        self,
        files: list[ProjectFile],
        include_empty_files: bool = False,
    ) -> str:
        """
        Build a safe repository tree.

        Ignored files such as .env and binary files
        are excluded from the tree as well as from
        file contents.
        """

        if not files:
            return (
                "PROJECT STRUCTURE\n\n"
                "(empty project)"
            )

        visible_files: list[ProjectFile] = []

        ignored_names = {
            name.lower()
            for name in self.IGNORED_FILE_NAMES
        }

        # -----------------------------------------------------
        # Filter files and folders.
        # -----------------------------------------------------

        for project_file in files:

            name = (
                project_file.name or ""
            ).strip()

            if not name:
                continue

            lower_name = name.lower()

            # -------------------------------------------------
            # FOLDER
            # -------------------------------------------------

            if project_file.type == "folder":

                if lower_name in ignored_names:
                    continue

                visible_files.append(
                    project_file
                )

                continue

            # -------------------------------------------------
            # FILE
            # -------------------------------------------------

            if (
                lower_name not in ignored_names
                and not any(
                    lower_name.endswith(extension)
                    for extension in self.IGNORED_EXTENSIONS
                )
                and (include_empty_files or project_file.content)
            ):
                visible_files.append(
                    project_file
                )

        if not visible_files:
            return (
                "PROJECT STRUCTURE\n\n"
                "(no AI-readable files)"
            )

        # -----------------------------------------------------
        # Create parent -> children mapping.
        # -----------------------------------------------------

        visible_ids = {
            project_file.id
            for project_file in visible_files
        }

        children: dict[
            int | None,
            list[ProjectFile],
        ] = {}

        for project_file in visible_files:

            parent_id = project_file.parent_id

            # If the parent is not visible, move the
            # item to the root instead of creating a
            # broken tree.
            if (
                parent_id is not None
                and parent_id not in visible_ids
            ):
                parent_id = None

            children.setdefault(
                parent_id,
                [],
            ).append(project_file)

        lines: list[str] = [
            "PROJECT STRUCTURE",
            "",
        ]

        # -----------------------------------------------------
        # Recursive tree rendering.
        # -----------------------------------------------------

        def add_children(
            parent_id: int | None,
            depth: int,
        ) -> None:

            items = children.get(
                parent_id,
                [],
            )

            items = sorted(
                items,
                key=lambda item: (
                    item.type != "folder",
                    (
                        item.name or ""
                    ).lower(),
                ),
            )

            for item in items:

                name = (
                    item.name or ""
                ).strip()

                if not name:
                    continue

                indentation = (
                    "  " * depth
                )

                if item.type == "folder":

                    lines.append(
                        f"{indentation}{name}/"
                    )

                    add_children(
                        item.id,
                        depth + 1,
                    )

                else:

                    lines.append(
                        f"{indentation}{name}"
                    )

        add_children(
            None,
            0,
        )

        return "\n".join(lines)

    # =========================================================
    # COMBINE CONTEXT
    # =========================================================

    def _combine_context(
        self,
        repository_tree: str,
        file_sections: list[str],
    ) -> str:
        """
        Combine repository structure and file
        contents into one AI context.
        """

        sections = [
            repository_tree,
            "",
            "PROJECT FILE CONTENT",
            "",
        ]

        sections.extend(
            file_sections
        )

        return "\n".join(
            sections
        ).strip()

    # =========================================================
    # CONTEXT LIMIT
    # =========================================================

    def _limit_context(
        self,
        context: str,
    ) -> str:
        """
        Prevent the repository context from
        becoming excessively large.
        """

        if len(context) <= self.max_chars:
            return context

        truncated = context[
            : self.max_chars
        ]

        return (
            truncated.rstrip()
            + "\n\n"
            + "[Repository context truncated "
            "because it exceeded the configured "
            "context limit.]"
        )

    # =========================================================
    # LANGUAGE DETECTION
    # =========================================================

    @staticmethod
    def _detect_language(
        file_name: str | None,
    ) -> str | None:
        """
        Detect programming language from
        the file extension.
        """

        if not file_name:
            return None

        lower_name = file_name.lower()

        extension_map = {
            ".py": "python",
            ".js": "javascript",
            ".jsx": "javascript",
            ".ts": "typescript",
            ".tsx": "typescript",
            ".java": "java",
            ".c": "c",
            ".cpp": "cpp",
            ".h": "c",
            ".hpp": "cpp",
            ".cs": "csharp",
            ".go": "go",
            ".rs": "rust",
            ".php": "php",
            ".rb": "ruby",
            ".swift": "swift",
            ".kt": "kotlin",
            ".html": "html",
            ".css": "css",
            ".scss": "scss",
            ".sql": "sql",
            ".json": "json",
            ".xml": "xml",
            ".yaml": "yaml",
            ".yml": "yaml",
            ".md": "markdown",
            ".sh": "bash",
            ".ps1": "powershell",
        }

        for extension, language in (
            extension_map.items()
        ):
            if lower_name.endswith(
                extension
            ):
                return language

        return None