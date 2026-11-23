import json
import re
from pathlib import PurePosixPath
from typing import cast

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from App.models.project import Project
from App.models.project_file import ProjectFile
from App.service.AI.context.repository_relevance import RepositoryRelevanceService
from App.service.AI.index.code_chunker import CodeChunker
from App.service.AI.index.vector_rag_service import VectorRAGService


class AgentToolError(ValueError):
    pass


class AgentToolArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ListFilesArguments(AgentToolArguments):
    path: str = Field(default="", max_length=2000)


class ReadFileArguments(AgentToolArguments):
    path: str = Field(min_length=1, max_length=2000)


class CreateFileArguments(AgentToolArguments):
    path: str = Field(min_length=1, max_length=2000)
    content: str = Field(max_length=20000)


class UpdateFileArguments(AgentToolArguments):
    path: str = Field(min_length=1, max_length=2000)
    content: str = Field(max_length=20000)


class DeleteFileArguments(AgentToolArguments):
    path: str = Field(min_length=1, max_length=2000)


class ExecuteCommandArguments(AgentToolArguments):
    command: str = Field(min_length=1, max_length=500)


class GetExecutionResultArguments(AgentToolArguments):
    pass


class AgentTools:
    MAX_INSPECT_CHARS = 25000
    MAX_SEARCH_RESULTS = 20
    MAX_PROFILE_FILES = 1000
    MAX_PROFILE_MANIFESTS = 8
    MAX_PROFILE_DEPENDENCIES = 10
    MAX_PROJECT_PROFILE_CHARS = 8000
    SEARCH_STOP_WORDS = {
        "a",
        "add",
        "an",
        "and",
        "build",
        "change",
        "create",
        "for",
        "generate",
        "implement",
        "in",
        "make",
        "of",
        "on",
        "please",
        "the",
        "to",
        "update",
        "using",
        "with",
    }
    SECRET_NAME_MARKERS = ("secret", "credential", "password", "private_key")
    PYTHON_IMPORT = re.compile(
        r"(?m)^\s*(?:from\s+([\w.]+)\s+import\s+([\w*., ]+)|"
        r"import\s+([\w.]+))"
    )
    MODULE_IMPORT = re.compile(
        r"""(?:\bfrom\s*|\bimport\s*\(\s*|\brequire\s*\(\s*)["']([^"']+)["']"""
    )
    LANGUAGE_BY_EXTENSION = {
        ".c": "C",
        ".cc": "C++",
        ".cpp": "C++",
        ".cs": "C#",
        ".css": "CSS",
        ".dart": "Dart",
        ".go": "Go",
        ".html": "HTML",
        ".java": "Java",
        ".js": "JavaScript",
        ".jsx": "JavaScript",
        ".kt": "Kotlin",
        ".php": "PHP",
        ".py": "Python",
        ".rb": "Ruby",
        ".rs": "Rust",
        ".scss": "SCSS",
        ".sh": "Shell",
        ".sql": "SQL",
        ".swift": "Swift",
        ".ts": "TypeScript",
        ".tsx": "TypeScript",
        ".vue": "Vue",
    }
    MANIFEST_NAMES = {
        "build.gradle": "Gradle build manifest",
        "build.gradle.kts": "Gradle Kotlin build manifest",
        "cargo.toml": "Rust package manifest",
        "composer.json": "PHP dependency manifest",
        "gemfile": "Ruby dependency manifest",
        "go.mod": "Go module manifest",
        "makefile": "Make build configuration",
        "package.json": "Node.js package manifest",
        "pom.xml": "Maven build manifest",
        "pyproject.toml": "Python project configuration",
        "setup.cfg": "Python project configuration",
        "setup.py": "Python project configuration",
    }
    ENTRY_POINT_NAMES = {
        "__main__.py",
        "app.py",
        "asgi.py",
        "cli.py",
        "index.js",
        "index.jsx",
        "index.ts",
        "index.tsx",
        "main.go",
        "main.java",
        "main.js",
        "main.py",
        "main.rs",
        "main.ts",
        "main.tsx",
        "manage.py",
        "server.js",
        "server.ts",
        "wsgi.py",
    }
    CONVENTION_NAMES = {
        ".editorconfig",
        ".eslintrc",
        ".eslintrc.json",
        ".flake8",
        ".prettierrc",
        ".prettierrc.json",
        ".ruff.toml",
        "eslint.config.cjs",
        "eslint.config.js",
        "eslint.config.mjs",
        "jest.config.js",
        "jest.config.ts",
        "makefile",
        "pytest.ini",
        "ruff.toml",
        "tsconfig.json",
        "tox.ini",
        "vitest.config.js",
        "vitest.config.ts",
    }
    FRAMEWORK_DEPENDENCIES = {
        "Django": {"django"},
        "Express": {"express"},
        "FastAPI": {"fastapi"},
        "Flask": {"flask"},
        "Next.js": {"next"},
        "React": {"react"},
        "Vite": {"vite"},
    }

    def __init__(self, db: Session, project_id: int, user_id: int):
        self.db = db
        self.project_id = project_id
        project = (
            db.query(Project)
            .filter(
                Project.id == project_id,
                Project.user_id == user_id,
            )
            .first()
        )
        if project is None:
            raise AgentToolError("Project not found.")
        self.project = project
        self._project_file_paths: list[str] | None = None

    def project_intelligence(self) -> str:
        paths = self.list_project_files()[:self.MAX_PROFILE_FILES]
        manifests = [
            path
            for path in paths
            if PurePosixPath(path).name.casefold() in self.MANIFEST_NAMES
            or (
                PurePosixPath(path).name.casefold().startswith("requirements")
                and PurePosixPath(path).suffix.casefold() == ".txt"
            )
        ][:self.MAX_PROFILE_MANIFESTS]
        manifest_summaries = []
        dependency_names: set[str] = set()
        package_metadata: dict[str, str] = {}
        package_scripts: dict[str, str] = {}
        for path in manifests:
            try:
                file_data = self.inspect_file(path)
            except AgentToolError:
                continue
            content = cast(str, file_data["content"])
            summary: dict[str, object] = {
                "path": path[:160],
                "type": self._manifest_type(path),
            }
            names, metadata, scripts = self._manifest_details(path, content)
            dependency_names.update(names)
            if metadata:
                package_metadata.update(metadata)
                summary.update(metadata)
            if scripts:
                package_scripts.update(scripts)
                summary["scripts"] = scripts
            if names:
                summary["dependencies"] = [
                    name[:80]
                    for name in sorted(names)[:self.MAX_PROFILE_DEPENDENCIES]
                ]
            manifest_summaries.append(summary)

        extension_counts: dict[str, int] = {}
        for path in paths:
            language = self.LANGUAGE_BY_EXTENSION.get(
                PurePosixPath(path).suffix.casefold()
            )
            if language:
                extension_counts[language] = extension_counts.get(language, 0) + 1

        entry_points = [
            path[:160] for path in paths
            if PurePosixPath(path).name.casefold() in self.ENTRY_POINT_NAMES
        ][:8]
        test_paths = [
            path[:160] for path in paths if self._is_test_path(path)
        ][:8]
        convention_paths = [
            path[:160]
            for path in paths
            if self._is_convention_path(path)
        ][:8]
        top_level_directories = sorted({
            PurePosixPath(path).parts[0][:160]
            for path in paths
            if len(PurePosixPath(path).parts) > 1
        })[:20]
        readme_commands = self._readme_commands(
            [path for path in paths if PurePosixPath(path).name.casefold().startswith("readme")][:2]
        )
        frameworks = sorted(
            framework
            for framework, names in self.FRAMEWORK_DEPENDENCIES.items()
            if names & {name.casefold() for name in dependency_names}
        )
        profile: dict[str, object] = {
            "project": {
                "name": cast(str, self.project.name),
                "description": (cast(str | None, self.project.description) or "")[:500],
                "configured_language": cast(str | None, self.project.language) or "unknown",
            },
            "safe_project_file_count": len(paths),
            "file_inventory_truncated": len(paths) == self.MAX_PROFILE_FILES,
            "detected_languages": dict(sorted(extension_counts.items())),
            "top_level_directories": top_level_directories,
            "manifests": manifest_summaries,
            "detected_frameworks": frameworks,
            "entry_point_candidates": entry_points,
            "test_file_candidates": test_paths,
            "convention_files": convention_paths,
            "readme_commands": readme_commands,
            "evidence_note": (
                "Detected from project file paths and manifest contents; "
                "entry points and conventions are candidates, not execution-verified."
            ),
        }
        if package_metadata:
            profile["package_metadata"] = package_metadata
        if package_scripts:
            profile["package_scripts"] = package_scripts
        serialized = json.dumps(profile, ensure_ascii=True, sort_keys=True)
        if len(serialized) > self.MAX_PROJECT_PROFILE_CHARS:
            profile["readme_commands"] = []
            profile["entry_point_candidates"] = entry_points[:4]
            profile["test_file_candidates"] = test_paths[:4]
            profile["convention_files"] = convention_paths[:4]
            profile["manifests"] = [
                {
                    key: value
                    for key, value in summary.items()
                    if key in {"path", "type", "package_name", "module"}
                }
                for summary in manifest_summaries[:4]
            ]
            profile.pop("package_scripts", None)
            serialized = json.dumps(profile, ensure_ascii=True, sort_keys=True)
        if len(serialized) > self.MAX_PROJECT_PROFILE_CHARS:
            profile = {
                "project": profile["project"],
                "safe_project_file_count": len(paths),
                "detected_languages": dict(sorted(extension_counts.items())),
                "evidence_note": (
                    "The project profile was summarized to fit the context limit; "
                    "some inventory items were omitted."
                ),
            }
            serialized = json.dumps(profile, ensure_ascii=True, sort_keys=True)
        return serialized

    @classmethod
    def _manifest_type(cls, file_path: str) -> str:
        name = PurePosixPath(file_path).name.casefold()
        if name.startswith("requirements") and name.endswith(".txt"):
            return "Python requirements"
        return cls.MANIFEST_NAMES.get(name, "Dependency or build manifest")

    @classmethod
    def _manifest_details(
        cls,
        file_path: str,
        content: str,
    ) -> tuple[set[str], dict[str, str], dict[str, str]]:
        name = PurePosixPath(file_path).name.casefold()
        dependencies: set[str] = set()
        metadata: dict[str, str] = {}
        scripts: dict[str, str] = {}
        if name == "package.json" or name == "composer.json":
            try:
                manifest = json.loads(content)
            except json.JSONDecodeError:
                return dependencies, {"parse_status": "invalid JSON"}, scripts
            if not isinstance(manifest, dict):
                return dependencies, {"parse_status": "unexpected JSON shape"}, scripts
            package_name = manifest.get("name")
            if isinstance(package_name, str) and package_name:
                metadata["package_name"] = package_name[:200]
            dependency_sections = (
                ("dependencies", "devDependencies", "peerDependencies")
                if name == "package.json"
                else ("require", "require-dev")
            )
            for section in dependency_sections:
                section_data = manifest.get(section)
                if isinstance(section_data, dict):
                    dependencies.update(
                        key.casefold()[:120]
                        for key in section_data
                        if isinstance(key, str) and key
                    )
            raw_scripts = manifest.get("scripts")
            if name == "package.json" and isinstance(raw_scripts, dict):
                scripts = {
                    key[:80]: value[:200]
                    for key, value in raw_scripts.items()
                    if isinstance(key, str)
                    and isinstance(value, str)
                    and key in {"build", "dev", "start", "test", "lint", "typecheck"}
                }
            return dependencies, metadata, scripts

        if name.startswith("requirements") and name.endswith(".txt"):
            for line in content.splitlines():
                match = re.match(
                    r"\s*([A-Za-z0-9][A-Za-z0-9_.-]*)"
                    r"(?:\[[^\]]+\])?\s*(?:[<>=!~;].*)?$",
                    line.split("#", 1)[0].strip(),
                )
                if match:
                    dependencies.add(match.group(1).casefold())
            return dependencies, metadata, scripts

        if name == "pyproject.toml":
            project_name = re.search(
                r"""(?m)^\s*name\s*=\s*["']([^"']+)["']""",
                content,
            )
            if project_name:
                metadata["package_name"] = project_name.group(1)[:200]
            dependency_blocks = re.finditer(
                r"""(?ms)^\s*(?:dependencies|optional-dependencies)\s*=\s*\[(.*?)\]""",
                content,
            )
            for block in dependency_blocks:
                dependencies.update(
                    match.casefold()
                    for match in re.findall(
                        r"""["']\s*([A-Za-z0-9_.-]+)(?:\[[^\]]+\])?\s*(?:[<>=!~].*)?["']""",
                        block.group(1),
                    )
                )
            return dependencies, metadata, scripts

        if name == "go.mod":
            module = re.search(r"(?m)^\s*module\s+(\S+)", content)
            if module:
                metadata["module"] = module.group(1)[:200]
            require_blocks = re.findall(r"(?ms)^\s*require\s*\((.*?)\)", content)
            for block in require_blocks:
                dependencies.update(
                    line.split()[0].casefold()
                    for line in block.splitlines()
                    if line.strip() and not line.lstrip().startswith("//")
                )
            return dependencies, metadata, scripts

        if name == "cargo.toml":
            section = ""
            for line in content.splitlines():
                header = re.match(r"\s*\[([^\]]+)\]", line)
                if header:
                    section = header.group(1).casefold()
                elif section in {"dependencies", "dev-dependencies", "build-dependencies"}:
                    dependency = re.match(r"\s*([A-Za-z0-9_-]+)\s*=", line)
                    if dependency:
                        dependencies.add(dependency.group(1).casefold())
                elif section == "package":
                    package_name = re.match(
                        r"""^\s*name\s*=\s*["']([^"']+)["']""",
                        line,
                    )
                    if package_name:
                        metadata["package_name"] = package_name.group(1)[:200]
            return dependencies, metadata, scripts
        return dependencies, metadata, scripts

    @staticmethod
    def _is_test_path(file_path: str) -> bool:
        path = file_path.casefold()
        name = PurePosixPath(path).name
        return (
            any(part in {"test", "tests", "__tests__", "spec", "specs"}
                for part in PurePosixPath(path).parts[:-1])
            or name.startswith("test_")
            or name.endswith(("_test.go", "_test.py"))
            or ".test." in name
            or ".spec." in name
        )

    @classmethod
    def _is_convention_path(cls, file_path: str) -> bool:
        name = PurePosixPath(file_path).name.casefold()
        return (
            name in cls.CONVENTION_NAMES
            or name.startswith((".prettierrc", "eslint.config.", "jest.config.", "vitest.config."))
        )

    def _readme_commands(self, readme_paths: list[str]) -> list[dict[str, str]]:
        command_pattern = re.compile(
            r"^\s*(?:python(?:3)?\s|pytest(?:\s|$)|npm\s|pnpm\s|"
            r"yarn\s|uv\s|poetry\s|cargo\s|go\s|make(?:\s|$))",
            re.IGNORECASE,
        )
        commands = []
        for path in readme_paths:
            try:
                content = cast(str, self.inspect_file(path)["content"])
            except AgentToolError:
                continue
            found = [
                line.strip()[:300]
                for line in content.splitlines()
                if command_pattern.match(line)
            ]
            if found:
                commands.append({
                    "path": path[:160],
                    "commands": " | ".join(found[:3]),
                })
        return commands[:2]

    def list_project_files(self) -> list[str]:
        if self._project_file_paths is not None:
            return list(self._project_file_paths)
        files = (
            self.db.query(ProjectFile)
            .filter(
                ProjectFile.project_id == self.project_id,
                ProjectFile.type == "file",
            )
            .order_by(ProjectFile.id.asc())
            .limit(1000)
            .all()
        )
        result = []
        for project_file in files:
            path = self.get_file_path(project_file)
            if self.is_safe_path(path):
                result.append(path)
        self._project_file_paths = result
        return list(result)

    def list_project_entries(self, directory: str = "") -> list[dict[str, str]]:
        normalized_directory = ""
        if directory not in {"", "."}:
            normalized_directory = self._validate_path(directory)
            folder = (
                self.db.query(ProjectFile)
                .filter(
                    ProjectFile.project_id == self.project_id,
                    ProjectFile.type == "folder",
                )
                .all()
            )
            if not any(
                self.get_file_path(item) == normalized_directory
                and self.is_safe_path(normalized_directory)
                for item in folder
            ):
                raise AgentToolError("Directory not found in this project.")

        entries: list[dict[str, str]] = []
        items = (
            self.db.query(ProjectFile)
            .filter(ProjectFile.project_id == self.project_id)
            .order_by(ProjectFile.id.asc())
            .all()
        )
        for item in items:
            path = self.get_file_path(item)
            if not self.is_safe_path(path):
                continue
            parent_path = PurePosixPath(path).parent.as_posix()
            if parent_path == ".":
                parent_path = ""
            if parent_path != normalized_directory:
                continue
            entries.append({
                "path": path,
                "type": cast(str, item.type),
            })
        return entries

    def inspect_file(self, file_path: str) -> dict[str, object]:
        project_file = self.resolve_file(file_path)
        content = cast(str | None, project_file.content) or ""
        if len(content) > self.MAX_INSPECT_CHARS:
            raise AgentToolError("File exceeds the agent inspection limit.")
        if self.contains_sensitive_content(content):
            raise AgentToolError("File content is protected from agent access.")
        return {
            "path": file_path,
            "language": cast(str | None, project_file.language),
            "content": content,
            "size": len(content.encode("utf-8")),
            "line_count": len(content.splitlines()),
        }

    def search_project(self, query: str) -> list[dict[str, object]]:
        terms = [
            term[:80]
            for term in re.findall(r"[A-Za-z0-9_]+", query.casefold())
            if len(term) > 1 and term not in self.SEARCH_STOP_WORDS
        ][:8]
        if not terms:
            return []
        files = (
            self.db.query(ProjectFile)
            .filter(
                ProjectFile.project_id == self.project_id,
                ProjectFile.type == "file",
            )
            .order_by(ProjectFile.id.asc())
            .limit(1000)
            .all()
        )
        matches: list[tuple[int, dict[str, object]]] = []
        for project_file in files:
            path = self.get_file_path(project_file)
            content = cast(str | None, project_file.content) or ""
            if not self.is_safe_path(path) or self.contains_sensitive_content(content):
                continue
            folded_path = path.casefold()
            folded_content = content.casefold()
            path_terms = [
                term for term in terms if term in folded_path
            ]
            content_terms = [
                term for term in terms if term in folded_content
            ]
            matching_terms = [
                term for term in terms
                if term in path_terms or term in content_terms
            ]
            if not matching_terms:
                continue
            score = len(path_terms) * 3 + len(content_terms)
            if "python" in terms and folded_path.endswith(".py"):
                score += 2
            content_lines = content.splitlines()
            line_number = next(
                (
                    index + 1
                    for index, line in enumerate(content_lines)
                    if any(term in line.casefold() for term in content_terms)
                ),
                0,
            )
            start = max(0, line_number - 3)
            snippet = "\n".join(content_lines[start:start + 6])[:1500]
            matches.append(
                (
                    score,
                    {
                        "path": path,
                        "line": line_number or None,
                        "snippet": snippet,
                        "matched_terms": matching_terms,
                    },
                )
            )
        matches.sort(key=lambda match: match[0], reverse=True)
        return [
            result
            for _, result in matches[:self.MAX_SEARCH_RESULTS]
        ]

    def find_file_relationships(
        self,
        file_paths: list[str],
        max_relationships: int = 12,
    ) -> list[tuple[str, str]]:
        relevant_paths = set(file_paths)
        if not relevant_paths:
            return []
        files = (
            self.db.query(ProjectFile)
            .filter(
                ProjectFile.project_id == self.project_id,
                ProjectFile.type == "file",
            )
            .order_by(ProjectFile.id.asc())
            .all()
        )
        safe_files = [
            (self.get_file_path(project_file), project_file)
            for project_file in files
            if self.is_safe_path(self.get_file_path(project_file))
        ]
        available_paths = {path for path, _ in safe_files}
        relationships: list[tuple[str, str]] = []
        seen: set[tuple[str, str]] = set()

        for source_path, project_file in safe_files:
            content = cast(str | None, project_file.content) or ""
            if (
                len(content) > self.MAX_INSPECT_CHARS
                or self.contains_sensitive_content(content)
            ):
                continue
            references: set[str] = set()
            for match in self.PYTHON_IMPORT.finditer(content):
                module = match.group(1) or match.group(3)
                imported_names = match.group(2)
                if module:
                    references.add(module)
                if imported_names:
                    references.update(
                        name.strip().split(" as ", 1)[0]
                        for name in imported_names.split(",")
                        if name.strip() and name.strip() != "*"
                    )
            references.update(
                match.group(1) for match in self.MODULE_IMPORT.finditer(content)
            )

            for reference in references:
                normalized = reference.replace("\\", "/")
                if normalized.startswith("."):
                    normalized = PurePosixPath(
                        source_path
                    ).parent.joinpath(normalized).as_posix()
                elif "/" not in normalized and normalized.rsplit(".", 1)[-1] not in {
                    "py", "js", "jsx", "ts", "tsx", "java",
                }:
                    normalized = normalized.replace(".", "/")
                for extension in (".py", ".js", ".jsx", ".ts", ".tsx", ".java"):
                    normalized = normalized.removesuffix(extension)
                normalized = normalized.strip("./").casefold()
                candidates = []
                for path in sorted(available_paths):
                    target_stem = path.casefold()
                    for extension in (".py", ".js", ".jsx", ".ts", ".tsx", ".java"):
                        target_stem = target_stem.removesuffix(extension)
                    if (
                        target_stem == normalized
                        or target_stem.endswith(f"/{normalized}")
                        or target_stem.rsplit("/", 1)[-1] == normalized.rsplit("/", 1)[-1]
                    ):
                        candidates.append(path)
                for target_path in candidates:
                    if source_path == target_path:
                        continue
                    if source_path not in relevant_paths and target_path not in relevant_paths:
                        continue
                    relationship = (source_path, target_path)
                    if relationship not in seen:
                        relationships.append(relationship)
                        seen.add(relationship)
                        if len(relationships) >= max_relationships:
                            return relationships
        return relationships

    def retrieve_context(self, query: str) -> str:
        relevance = RepositoryRelevanceService(
            db=self.db,
            project_id=self.project_id,
            max_chars=20000,
            max_files=6,
        ).build_context(query=query)
        context_sections = []
        vector_result = VectorRAGService(
            db=self.db,
            project_id=self.project_id,
        ).search_project_context(
            project_id=self.project_id,
            query=query,
            top_k=5,
        )
        vector_sections = []
        vector_size = 0
        vector_paths: set[str] = set()
        for chunk in vector_result.chunks:
            if (
                not self.is_safe_path(chunk.file_path)
                or self.contains_sensitive_content(chunk.content)
            ):
                continue
            section = (
                f"FILE: {chunk.file_path} LINES: {chunk.start_line}-{chunk.end_line}\n"
                f"{chunk.content[:6000]}"
            )
            if vector_size + len(section) > 20000:
                break
            vector_sections.append(section)
            vector_paths.add(chunk.file_path)
            vector_size += len(section)
        for result in relevance.search_results:
            if result.path in vector_paths:
                continue
            if (
                not self.is_safe_path(result.path)
                or self.contains_sensitive_content(result.content)
            ):
                continue
            section = f"FILE: {result.path}\n{result.content[:6000]}"
            if vector_size + len(section) > 20000:
                break
            context_sections.append(section)
            vector_size += len(section)
        return "\n\n".join([*vector_sections, *context_sections])[:20000]

    def resolve_file(self, file_path: str) -> ProjectFile:
        path = self._validate_path(file_path)
        if not self.is_safe_path(path):
            raise AgentToolError("This file is protected from agent access.")
        files = (
            self.db.query(ProjectFile)
            .filter(
                ProjectFile.project_id == self.project_id,
                ProjectFile.type == "file",
            )
            .all()
        )
        for project_file in files:
            if self.get_file_path(project_file) == path:
                return project_file
        raise AgentToolError("File not found in this project.")

    def get_file_path(self, project_file: ProjectFile) -> str:
        parts = [cast(str, project_file.name)]
        parent_id = cast(int | None, project_file.parent_id)
        visited = {cast(int, project_file.id)}
        while parent_id is not None:
            if parent_id in visited:
                raise AgentToolError("Project file hierarchy is invalid.")
            visited.add(parent_id)
            parent = (
                self.db.query(ProjectFile)
                .filter(
                    ProjectFile.id == parent_id,
                    ProjectFile.project_id == self.project_id,
                    ProjectFile.type == "folder",
                )
                .first()
            )
            if parent is None:
                raise AgentToolError("Project file hierarchy is invalid.")
            parts.append(cast(str, parent.name))
            parent_id = cast(int | None, parent.parent_id)
        return "/".join(reversed(parts))

    @staticmethod
    def _validate_path(file_path: str) -> str:
        if (
            not file_path
            or "\x00" in file_path
            or "\\" in file_path
            or any(part in {"", ".", ".."} for part in file_path.split("/"))
        ):
            raise AgentToolError("Invalid project file path.")
        path = PurePosixPath(file_path)
        if path.is_absolute():
            raise AgentToolError("Invalid project file path.")
        return path.as_posix()

    @classmethod
    def is_safe_path(cls, path: str) -> bool:
        lowered_parts = [part.lower() for part in PurePosixPath(path).parts]
        return (
            CodeChunker.is_indexable_path(path)
            and not any(
                part.startswith(".env")
                or any(marker in part for marker in cls.SECRET_NAME_MARKERS)
                for part in lowered_parts
            )
        )

    @classmethod
    def contains_sensitive_content(cls, content: str | None) -> bool:
        return CodeChunker.contains_sensitive_content(content)