import {
    useEffect,
    useState,
} from "react";

import type {
    MouseEvent,
} from "react";

import {
    ChevronDown,
    ChevronRight,
    File,
    Folder,
    FolderOpen,
    Plus,
    RefreshCw,
    MoreVertical,
} from "lucide-react";

import {
    getProjectFileTree,
} from "../../services/fileService";

import type {
    FileTreeNode,
} from "../../types/file";


// =========================================
// Props
// =========================================

interface FileExplorerProps {
    projectId: number;

    onFileSelect?: (
        file: FileTreeNode
    ) => void;

    onCreateFile?: (
        parentId: number | null
    ) => void;

    onCreateFolder?: (
        parentId: number | null
    ) => void;

    onRename?: (
        node: FileTreeNode
    ) => void;

    onDelete?: (
        node: FileTreeNode
    ) => void;
}


// =========================================
// File Tree Item Props
// =========================================

interface FileTreeItemProps {
    node: FileTreeNode;

    level: number;

    selectedFileId?: number | null;

    onFileSelect?: (
        file: FileTreeNode
    ) => void;

    onCreateFile?: (
        parentId: number | null
    ) => void;

    onCreateFolder?: (
        parentId: number | null
    ) => void;

    onRename?: (
        node: FileTreeNode
    ) => void;

    onDelete?: (
        node: FileTreeNode
    ) => void;
}


// =========================================
// File Tree Item
// =========================================

function FileTreeItem({
    node,
    level,
    selectedFileId,
    onFileSelect,
    onCreateFile,
    onCreateFolder,
    onRename,
    onDelete,
}: FileTreeItemProps) {

    const [
        isExpanded,
        setIsExpanded,
    ] = useState(true);


    const [
        showMenu,
        setShowMenu,
    ] = useState(false);


    const isFolder =
        node.type === "folder";


    const hasChildren =
        isFolder &&
        Boolean(
            node.children &&
            node.children.length > 0
        );


    const isSelected =
        selectedFileId === node.id;


    // =====================================
    // Tree Item Click
    // =====================================

    function handleClick() {

        console.log(
            "Tree item clicked:",
            node
        );


        if (isFolder) {

            setIsExpanded(
                current => !current
            );

            return;
        }


        onFileSelect?.(node);
    }


    // =====================================
    // Menu Click
    // =====================================

    function handleMenuClick(
        event: MouseEvent
    ) {

        event.stopPropagation();


        console.log(
            "Menu clicked:",
            node.name
        );


        setShowMenu(
            current => !current
        );
    }


    // =====================================
    // Create File
    // =====================================

    function handleCreateFile() {

        console.log(
            "CREATE FILE clicked. Parent:",
            node.id
        );


        setShowMenu(false);


        onCreateFile?.(
            node.id
        );
    }


    // =====================================
    // Create Folder
    // =====================================

    function handleCreateFolder() {

        console.log(
            "CREATE FOLDER clicked. Parent:",
            node.id
        );


        setShowMenu(false);


        onCreateFolder?.(
            node.id
        );
    }


    // =====================================
    // Rename
    // =====================================

    function handleRename() {

        console.log(
            "RENAME clicked:",
            node
        );


        setShowMenu(false);


        onRename?.(
            node
        );
    }


    // =====================================
    // Delete
    // =====================================

    function handleDelete() {

        console.log(
            "DELETE clicked:",
            node
        );


        setShowMenu(false);


        onDelete?.(
            node
        );
    }


    // =====================================
    // Render
    // =====================================

    return (
        <div className="file-tree-container">

            <div
                className="file-tree-row"
                style={{
                    paddingLeft:
                        `${12 + level * 18}px`,
                }}
            >

                {/* =========================
                    File / Folder
                   ========================= */}

                <button
                    type="button"
                    className={
                        `file-tree-item ${
                            isSelected
                                ? "selected"
                                : ""
                        }`
                    }
                    onClick={
                        handleClick
                    }
                    title={
                        node.path ??
                        node.name
                    }
                >

                    {/* Expand / Collapse Icon */}

                    {isFolder ? (

                        hasChildren ? (

                            isExpanded ? (

                                <ChevronDown
                                    size={16}
                                    strokeWidth={1.8}
                                />

                            ) : (

                                <ChevronRight
                                    size={16}
                                    strokeWidth={1.8}
                                />

                            )

                        ) : (

                            <span
                                style={{
                                    width: 16,
                                    flexShrink: 0,
                                }}
                            />

                        )

                    ) : (

                        <span
                            style={{
                                width: 16,
                                flexShrink: 0,
                            }}
                        />

                    )}


                    {/* File / Folder Icon */}

                    {isFolder ? (

                        isExpanded ? (

                            <FolderOpen
                                size={17}
                                strokeWidth={1.8}
                            />

                        ) : (

                            <Folder
                                size={17}
                                strokeWidth={1.8}
                            />

                        )

                    ) : (

                        <File
                            size={17}
                            strokeWidth={1.8}
                        />

                    )}


                    {/* Name */}

                    <span>
                        {node.name}
                    </span>

                </button>


                {/* =========================
                    More Menu Button
                   ========================= */}

                <button
                    type="button"
                    className="file-tree-menu-button"
                    onClick={
                        handleMenuClick
                    }
                    title="More actions"
                >

                    <MoreVertical
                        size={15}
                    />

                </button>


                {/* =========================
                    Context Menu
                   ========================= */}

                {showMenu && (

                    <div
                        className="file-tree-menu"
                        onClick={
                            event =>
                                event.stopPropagation()
                        }
                    >

                        {/* New File */}

                        {isFolder && (

                            <button
                                type="button"
                                onClick={
                                    handleCreateFile
                                }
                            >
                                New File
                            </button>

                        )}


                        {/* New Folder */}

                        {isFolder && (

                            <button
                                type="button"
                                onClick={
                                    handleCreateFolder
                                }
                            >
                                New Folder
                            </button>

                        )}


                        {/* Rename */}

                        <button
                            type="button"
                            onClick={
                                handleRename
                            }
                        >
                            Rename
                        </button>


                        {/* Delete */}

                        <button
                            type="button"
                            className="delete-menu-item"
                            onClick={
                                handleDelete
                            }
                        >
                            Delete
                        </button>

                    </div>

                )}

            </div>


            {/* =========================
                Children
               ========================= */}

            {isFolder &&
                isExpanded &&
                hasChildren && (

                    <div>

                        {node.children!.map(
                            child => (

                                <FileTreeItem
                                    key={
                                        child.id
                                    }

                                    node={
                                        child
                                    }

                                    level={
                                        level + 1
                                    }

                                    selectedFileId={
                                        selectedFileId
                                    }

                                    onFileSelect={
                                        onFileSelect
                                    }

                                    onCreateFile={
                                        onCreateFile
                                    }

                                    onCreateFolder={
                                        onCreateFolder
                                    }

                                    onRename={
                                        onRename
                                    }

                                    onDelete={
                                        onDelete
                                    }
                                />

                            )
                        )}

                    </div>

                )}

        </div>
    );
}


// =========================================
// File Explorer
// =========================================

export default function FileExplorer({
    projectId,
    onFileSelect,
    onCreateFile,
    onCreateFolder,
    onRename,
    onDelete,
}: FileExplorerProps) {

    const [
        tree,
        setTree,
    ] = useState<FileTreeNode[]>([]);


    const [
        isLoading,
        setIsLoading,
    ] = useState(true);


    const [
        error,
        setError,
    ] = useState("");


    const [
        selectedFileId,
        setSelectedFileId,
    ] = useState<number | null>(
        null
    );


    // =====================================
    // Load File Tree
    // =====================================

    async function loadTree() {

        console.log(
            "Loading file tree for project:",
            projectId
        );


        try {

            setError("");

            setIsLoading(true);


            const data =
                await getProjectFileTree(
                    projectId
                );


            console.log(
                "File tree received:",
                data
            );


            setTree(data);

        } catch (error: any) {

            console.error(
                "Failed to load file tree:",
                error
            );


            setError(
                error.response?.data?.detail ??
                "Unable to load project files."
            );

        } finally {

            setIsLoading(false);

        }
    }


    // =====================================
    // Load Tree On Project Change
    // =====================================

    useEffect(() => {

        setSelectedFileId(
            null
        );


        loadTree();

    }, [projectId]);


    // =====================================
    // New File From Explorer Header
    // =====================================

    function handleNewFile() {

        console.log(
            "TOP NEW FILE BUTTON CLICKED"
        );


        console.log(
            "onCreateFile function:",
            onCreateFile
        );


        if (!onCreateFile) {

            console.error(
                "ERROR: onCreateFile is undefined!"
            );

            return;
        }


        onCreateFile(null);
    }


    // =====================================
    // File Selection
    // =====================================

    function handleFileSelect(
        file: FileTreeNode
    ) {

        console.log(
            "Explorer selected file:",
            file
        );


        setSelectedFileId(
            file.id
        );


        onFileSelect?.(
            file
        );
    }


    // =====================================
    // Render
    // =====================================

    return (
        <aside className="file-explorer">

            {/* =================================
                Explorer Header
               ================================= */}

            <div className="explorer-header">

                <span>
                    EXPLORER
                </span>


                <div className="explorer-header-actions">

                    {/* New File */}

                    <button
                        type="button"
                        title="New File"
                        onClick={
                            handleNewFile
                        }
                    >

                        <Plus
                            size={15}
                        />

                    </button>


                    {/* Refresh */}

                    <button
                        type="button"
                        title="Refresh Explorer"
                        onClick={
                            loadTree
                        }
                        disabled={
                            isLoading
                        }
                    >

                        <RefreshCw
                            size={15}
                        />

                    </button>

                </div>

            </div>


            {/* =================================
                Project Section
               ================================= */}

            <div className="explorer-project-name">

                <span>
                    PROJECT FILES
                </span>

            </div>


            {/* =================================
                File Tree
               ================================= */}

            <div className="explorer-tree">

                {/* Loading */}

                {isLoading ? (

                    <div className="explorer-message">

                        Loading...

                    </div>

                ) : error ? (

                    /* Error */

                    <div className="explorer-error">

                        {error}

                        <br />

                        <button
                            type="button"
                            onClick={
                                loadTree
                            }
                        >
                            Retry
                        </button>

                    </div>

                ) : tree.length === 0 ? (

                    /* Empty */

                    <div className="explorer-message">

                        <p>
                            No files yet.
                        </p>


                        <button
                            type="button"
                            onClick={
                                handleNewFile
                            }
                        >

                            Create File

                        </button>

                    </div>

                ) : (

                    /* Tree */

                    tree.map(
                        node => (

                            <FileTreeItem
                                key={
                                    node.id
                                }

                                node={
                                    node
                                }

                                level={0}

                                selectedFileId={
                                    selectedFileId
                                }

                                onFileSelect={
                                    handleFileSelect
                                }

                                onCreateFile={
                                    onCreateFile
                                }

                                onCreateFolder={
                                    onCreateFolder
                                }

                                onRename={
                                    onRename
                                }

                                onDelete={
                                    onDelete
                                }
                            />

                        )
                    )

                )}

            </div>

        </aside>
    );
}