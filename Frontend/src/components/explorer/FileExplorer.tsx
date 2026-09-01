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
    FilePlus,
    FolderPlus,
    Pencil,
    Trash2,
} from "lucide-react";

import {
    getProjectFileTree,
} from "../../services/fileService";

import type {
    FileTreeNode,
} from "../../types/file";


// ============================================================
// FileExplorer Props
// ============================================================

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


// ============================================================
// FileTreeItem Props
// ============================================================

interface FileTreeItemProps {

    node: FileTreeNode;

    level: number;

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


// ============================================================
// Context Menu State
// ============================================================

interface ContextMenuPosition {

    x: number;

    y: number;
}


// ============================================================
// File Tree Item
// ============================================================

function FileTreeItem({
    node,
    level,
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


    const [
        menuPosition,
        setMenuPosition,
    ] = useState<ContextMenuPosition | null>(
        null
    );


    const isFolder =
        node.type === "folder";


    const hasChildren =
        isFolder &&
        Boolean(
            node.children &&
            node.children.length > 0
        );


    // ========================================================
    // Close Context Menu
    // ========================================================

    function closeMenu() {

        setShowMenu(false);

        setMenuPosition(null);
    }


    // ========================================================
    // Calculate Menu Position
    // ========================================================

    function calculateMenuPosition(
        x: number,
        y: number
    ): ContextMenuPosition {

        const menuWidth = 170;

        const menuHeight =
            isFolder
                ? 180
                : 130;


        const padding = 8;


        let finalX = x;

        let finalY = y;


        if (
            finalX + menuWidth >
            window.innerWidth
        ) {

            finalX =
                window.innerWidth -
                menuWidth -
                padding;
        }


        if (
            finalY + menuHeight >
            window.innerHeight
        ) {

            finalY =
                window.innerHeight -
                menuHeight -
                padding;
        }


        if (finalX < padding) {

            finalX = padding;
        }


        if (finalY < padding) {

            finalY = padding;
        }


        return {
            x: finalX,
            y: finalY,
        };
    }


    // ========================================================
    // Open Context Menu
    // ========================================================

    function openContextMenu(
        x: number,
        y: number
    ) {

        console.log(
            "Opening context menu:",
            node
        );


        const position =
            calculateMenuPosition(
                x,
                y
            );


        setMenuPosition(
            position
        );


        setShowMenu(true);
    }


    // ========================================================
    // Normal Tree Item Click
    // ========================================================

    function handleClick() {

        console.log(
            "Tree item clicked:",
            node
        );


        if (isFolder) {

            setIsExpanded(
                current =>
                    !current
            );

            return;
        }


        onFileSelect?.(
            node
        );
    }


    // ========================================================
    // Right Click
    // ========================================================

    function handleContextMenu(
        event: MouseEvent
    ) {

        event.preventDefault();

        event.stopPropagation();


        console.log(
            "Right-clicked:",
            node
        );


        openContextMenu(
            event.clientX,
            event.clientY
        );
    }


    // ========================================================
    // Three Dot Menu
    // ========================================================

    function handleMenuClick(
        event: MouseEvent
    ) {

        event.preventDefault();

        event.stopPropagation();


        console.log(
            "Three-dot menu clicked:",
            node.name
        );


        if (showMenu) {

            closeMenu();

            return;
        }


        const target =
            event.currentTarget as HTMLElement;


        const rect =
            target.getBoundingClientRect();


        openContextMenu(
            rect.right - 10,
            rect.bottom + 4
        );
    }


    // ========================================================
    // Create File
    // ========================================================

    function handleCreateFile() {

        console.log(
            "CREATE FILE:",
            node.id
        );


        closeMenu();


        onCreateFile?.(
            node.id
        );
    }


    // ========================================================
    // Create Folder
    // ========================================================

    function handleCreateFolder() {

        console.log(
            "CREATE FOLDER:",
            node.id
        );


        closeMenu();


        onCreateFolder?.(
            node.id
        );
    }


    // ========================================================
    // Rename
    // ========================================================

    function handleRename() {

        console.log(
            "RENAME:",
            node
        );


        closeMenu();


        onRename?.(
            node
        );
    }


    // ========================================================
    // Delete
    // ========================================================

    function handleDelete() {

        console.log(
            "DELETE:",
            node
        );


        closeMenu();


        onDelete?.(
            node
        );
    }


    return (
        <div
            className="file-tree-container"
            onContextMenu={
                handleContextMenu
            }
        >

            {/* =================================================
                Tree Row
            ================================================= */}

            <div
                className="file-tree-row"
                style={{
                    paddingLeft:
                        `${12 + level * 18}px`,
                }}
            >

                {/* =============================================
                    File / Folder Button
                ============================================= */}

                <button
                    type="button"
                    className="file-tree-item"
                    onClick={
                        handleClick
                    }
                >

                    {/* Folder Expand Icon */}

                    {isFolder ? (

                        hasChildren ? (

                            isExpanded ? (

                                <ChevronDown
                                    size={16}
                                />

                            ) : (

                                <ChevronRight
                                    size={16}
                                />

                            )

                        ) : (

                            <span
                                style={{
                                    width: 16,
                                }}
                            />

                        )

                    ) : (

                        <span
                            style={{
                                width: 16,
                            }}
                        />

                    )}


                    {/* File / Folder Icon */}

                    {isFolder ? (

                        isExpanded ? (

                            <FolderOpen
                                size={17}
                            />

                        ) : (

                            <Folder
                                size={17}
                            />

                        )

                    ) : (

                        <File
                            size={17}
                        />

                    )}


                    {/* File Name */}

                    <span>
                        {node.name}
                    </span>

                </button>


                {/* =============================================
                    Three Dot Button
                ============================================= */}

                <button
                    type="button"
                    className="file-tree-menu-button"
                    title="More actions"
                    onMouseDown={
                        event =>
                            event.stopPropagation()
                    }
                    onClick={
                        handleMenuClick
                    }
                >

                    <MoreVertical
                        size={15}
                    />

                </button>


                {/* =============================================
                    Context Menu
                ============================================= */}

                {showMenu &&
                    menuPosition && (

                        <div
                            className="file-tree-menu"
                            style={{
                                position: "fixed",
                                top:
                                    menuPosition.y,
                                left:
                                    menuPosition.x,
                                right: "auto",
                            }}
                            onContextMenu={
                                event =>
                                    event.preventDefault()
                            }
                            onMouseDown={
                                event =>
                                    event.stopPropagation()
                            }
                        >

                            {/* =====================================
                            Folder Actions
                        ===================================== */}

                            {isFolder && (

                                <>

                                    <button
                                        type="button"
                                        onClick={
                                            handleCreateFile
                                        }
                                    >

                                        <FilePlus
                                            size={15}
                                        />

                                        <span>
                                            New File
                                        </span>

                                    </button>


                                    <button
                                        type="button"
                                        onClick={
                                            handleCreateFolder
                                        }
                                    >

                                        <FolderPlus
                                            size={15}
                                        />

                                        <span>
                                            New Folder
                                        </span>

                                    </button>

                                </>

                            )}


                            {/* =====================================
                            Rename
                        ===================================== */}

                            <button
                                type="button"
                                onClick={
                                    handleRename
                                }
                            >

                                <Pencil
                                    size={15}
                                />

                                <span>
                                    Rename
                                </span>

                            </button>


                            {/* =====================================
                            Delete
                        ===================================== */}

                            <button
                                type="button"
                                className="delete-menu-item"
                                onClick={
                                    handleDelete
                                }
                            >

                                <Trash2
                                    size={15}
                                />

                                <span>
                                    Delete
                                </span>

                            </button>

                        </div>

                    )}

            </div>


            {/* =================================================
                Children
            ================================================= */}

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


// ============================================================
// Main File Explorer
// ============================================================

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


    // ========================================================
    // Load Tree
    // ========================================================

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


            setTree(
                data
            );

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


    // ========================================================
    // Initial Load
    // ========================================================

    useEffect(() => {

        loadTree();

    }, [projectId]);


    // ========================================================
    // Close Menus Globally
    // ========================================================

    useEffect(() => {

        function handleDocumentMouseDown(
            event: globalThis.MouseEvent
        ) {

            const target =
                event.target as HTMLElement;


            if (
                target.closest(
                    ".file-tree-menu"
                )
            ) {

                return;
            }


            if (
                target.closest(
                    ".file-tree-menu-button"
                )
            ) {

                return;
            }


            const menus =
                document.querySelectorAll(
                    ".file-tree-menu"
                );


            menus.forEach(
                menu => {

                    (
                        menu as HTMLElement
                    ).style.display =
                        "none";

                }
            );
        }


        function handleEscape(
            event: KeyboardEvent
        ) {

            if (
                event.key === "Escape"
            ) {

                const menus =
                    document.querySelectorAll(
                        ".file-tree-menu"
                    );


                menus.forEach(
                    menu => {

                        (
                            menu as HTMLElement
                        ).style.display =
                            "none";

                    }
                );
            }
        }


        document.addEventListener(
            "mousedown",
            handleDocumentMouseDown
        );


        document.addEventListener(
            "keydown",
            handleEscape
        );


        return () => {

            document.removeEventListener(
                "mousedown",
                handleDocumentMouseDown
            );


            document.removeEventListener(
                "keydown",
                handleEscape
            );
        };

    }, []);


    // ========================================================
    // Top New File
    // ========================================================

    function handleNewFile() {

        console.log(
            "TOP NEW FILE BUTTON CLICKED"
        );


        console.log(
            "onCreateFile:",
            onCreateFile
        );


        if (!onCreateFile) {

            console.error(
                "ERROR: onCreateFile is undefined!"
            );

            return;
        }


        onCreateFile(
            null
        );
    }


    // ========================================================
    // Render
    // ========================================================

    return (
        <aside className="file-explorer">

            {/* =================================================
                Explorer Header
            ================================================= */}

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
                        title="Refresh"
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


            {/* =================================================
                Project Files Header
            ================================================= */}

            <div className="explorer-project-name">

                PROJECT FILES

            </div>


            {/* =================================================
                File Tree
            ================================================= */}

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
                    )

                )}

            </div>

        </aside>
    );
}