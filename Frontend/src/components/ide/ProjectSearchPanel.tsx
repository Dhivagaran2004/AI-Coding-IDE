import { useCallback, useEffect, useMemo, useState } from "react";
import { isAxiosError } from "axios";
import { RefreshCw, Search } from "lucide-react";
import { getProjectFiles } from "../../services/fileService";
import type { FileTreeNode, ProjectFile } from "../../types/file";

interface ProjectSearchPanelProps {
    projectId: number;
    onFileSelect: (file: FileTreeNode) => void;
}

interface SearchResult {
    file: ProjectFile;
    line: number | null;
    preview: string;
}

function getFileLoadError(error: unknown): string {
    const detail = isAxiosError(error)
        ? error.response?.data?.detail
        : null;
    return typeof detail === "string"
        ? detail
        : error instanceof Error
            ? error.message
            : "Unable to load project files for search.";
}

export default function ProjectSearchPanel({
    projectId,
    onFileSelect,
}: ProjectSearchPanelProps) {
    const [files, setFiles] = useState<ProjectFile[]>([]);
    const [query, setQuery] = useState("");
    const [isLoading, setIsLoading] = useState(true);
    const [error, setError] = useState("");

    const readFiles = useCallback(
        () => getProjectFiles(projectId),
        [projectId],
    );

    useEffect(() => {
        let active = true;
        void readFiles()
            .then((projectFiles) => {
                if (active) {
                    setFiles(projectFiles);
                }
            })
            .catch((loadError: unknown) => {
                if (active) {
                    setError(getFileLoadError(loadError));
                }
            })
            .finally(() => {
                if (active) {
                    setIsLoading(false);
                }
            });
        return () => {
            active = false;
        };
    }, [readFiles]);

    async function refreshFiles() {
        setIsLoading(true);
        setError("");
        try {
            setFiles(await readFiles());
        } catch (loadError) {
            setError(getFileLoadError(loadError));
        } finally {
            setIsLoading(false);
        }
    }

    const results = useMemo(() => {
        const searchTerm = query.trim().toLocaleLowerCase();
        if (!searchTerm) {
            return [];
        }

        const matches: SearchResult[] = [];
        for (const file of files) {
            if (file.type !== "file") {
                continue;
            }

            const path = file.path ?? file.name;
            if (path.toLocaleLowerCase().includes(searchTerm)) {
                matches.push({ file, line: null, preview: path });
            }

            const lines = (file.content ?? "").split(/\r?\n/);
            for (let index = 0; index < lines.length; index += 1) {
                if (lines[index].toLocaleLowerCase().includes(searchTerm)) {
                    matches.push({
                        file,
                        line: index + 1,
                        preview: lines[index].trim() || "(empty line)",
                    });
                    if (matches.length >= 100) {
                        return matches;
                    }
                }
            }
        }
        return matches;
    }, [files, query]);

    return (
        <section className="ide-tool-view" aria-label="Workspace search">
            <header className="ide-tool-view__header">
                <h2>Search</h2>
                <button
                    type="button"
                    title="Refresh workspace files"
                    aria-label="Refresh workspace files"
                    onClick={() => void refreshFiles()}
                    disabled={isLoading}
                >
                    <RefreshCw size={15} />
                </button>
            </header>
            <label className="ide-search-field">
                <Search size={14} aria-hidden="true" />
                <input
                    type="search"
                    value={query}
                    onChange={(event) => setQuery(event.target.value)}
                    placeholder="Search files and contents"
                    aria-label="Search files and contents"
                />
            </label>
            <div className="ide-tool-view__body" aria-live="polite">
                {isLoading ? (
                    <p className="ide-tool-view__message">Loading project files…</p>
                ) : error ? (
                    <p className="ide-tool-view__error" role="alert">{error}</p>
                ) : !query.trim() ? (
                    <p className="ide-tool-view__message">
                        Search file names and contents in this project.
                    </p>
                ) : results.length === 0 ? (
                    <p className="ide-tool-view__message">No matching files or lines.</p>
                ) : (
                    <>
                        <p className="ide-search-count">
                            {results.length === 100 ? "100+" : results.length} results
                        </p>
                        {results.map((result, index) => {
                            const file = result.file;
                            const node: FileTreeNode = {
                                id: file.id,
                                name: file.name,
                                type: "file",
                                parent_id: file.parent_id,
                                path: file.path,
                            };
                            return (
                                <button
                                    key={`${file.id}-${result.line ?? "name"}-${index}`}
                                    type="button"
                                    className="ide-search-result"
                                    onClick={() => onFileSelect(node)}
                                    title={`Open ${file.path ?? file.name}`}
                                >
                                    <span className="ide-search-result__path">
                                        {file.path ?? file.name}
                                        {result.line !== null && `:${result.line}`}
                                    </span>
                                    {result.line !== null && (
                                        <span className="ide-search-result__preview">
                                            {result.preview}
                                        </span>
                                    )}
                                </button>
                            );
                        })}
                    </>
                )}
            </div>
        </section>
    );
}
