import {
    useEffect,
    useState,
    useRef,
} from "react";

import CodeEditor
    from "../components/editor/CodeEditor";

import Terminal
    from "../components/terminal/Terminal";

import {
    getProjectFiles,
    createProjectFile,
    updateProjectFile,
    deleteProjectFile,
} from "../services/fileService";

import {
    getLanguageFromFileName,
} from "../utils/language";

import {
    useNavigate,
    useParams,
} from "react-router-dom";

import {
    ArrowLeft,
    Code2,
    Save,
    X,
} from "lucide-react";

import FileExplorer from
    "../components/explorer/FileExplorer";

import type {
    FileTreeNode,
    ProjectFileCreate,
} from "../types/file";

import AIChat from "../components/ai/AIChat";

// =========================================
// Editor Tab Type
// =========================================

interface EditorTab {

    file: FileTreeNode;

    content: string;

    language: string;

    isDirty: boolean;
}


// =========================================
// IDE Page
// =========================================

export default function IDEPage() {

    const navigate = useNavigate();


    const {
        projectId,
    } = useParams();


    // =========================================
    // Editor Tabs
    // =========================================

    const [
        tabs,
        setTabs,
    ] = useState<EditorTab[]>([]);


    const [
        activeTabId,
        setActiveTabId,
    ] = useState<number | null>(
        null
    );


    // =========================================
    // Save State
    // =========================================

    const [
        isSaving,
        setIsSaving,
    ] = useState(false);


    const [
        saveError,
        setSaveError,
    ] = useState("");


    // =========================================
    // Explorer Refresh
    // =========================================

    const [
        refreshKey,
        setRefreshKey,
    ] = useState(0);


    // =========================================
    // Resizable Panels State
    // =========================================

    const [
        explorerWidth,
        setExplorerWidth,
    ] = useState<number>(() => {
        const saved = localStorage.getItem("ide_explorer_width");
        return saved ? Math.max(160, Math.min(600, parseInt(saved, 10))) : 240;
    });

    const [
        aiPanelWidth,
        setAiPanelWidth,
    ] = useState<number>(() => {
        const saved = localStorage.getItem("ide_ai_panel_width");
        return saved ? Math.max(260, Math.min(800, parseInt(saved, 10))) : 360;
    });

    const [
        terminalHeight,
        setTerminalHeight,
    ] = useState<number>(() => {
        const saved = localStorage.getItem("ide_terminal_height");
        return saved ? Math.max(80, Math.min(600, parseInt(saved, 10))) : 240;
    });

    const [
        activeResizer,
        setActiveResizer,
    ] = useState<"explorer" | "terminal" | "ai" | null>(null);

    const workspaceRef = useRef<HTMLDivElement | null>(null);
    const centerRef = useRef<HTMLDivElement | null>(null);

    // Synchronize to localStorage
    useEffect(() => {
        localStorage.setItem("ide_explorer_width", String(explorerWidth));
    }, [explorerWidth]);

    useEffect(() => {
        localStorage.setItem("ide_ai_panel_width", String(aiPanelWidth));
    }, [aiPanelWidth]);

    useEffect(() => {
        localStorage.setItem("ide_terminal_height", String(terminalHeight));
    }, [terminalHeight]);

    // Resizing mousemove & mouseup listeners
    useEffect(() => {
        if (!activeResizer) return;

        function handleMouseMove(event: MouseEvent) {
            if (!activeResizer) return;

            if (activeResizer === "explorer") {
                if (!workspaceRef.current) return;
                const rect = workspaceRef.current.getBoundingClientRect();
                const newWidth = event.clientX - rect.left;
                const minWidth = 160;
                const maxWidth = Math.max(minWidth, Math.min(600, rect.width - 450));
                const clamped = Math.max(minWidth, Math.min(maxWidth, newWidth));
                setExplorerWidth(clamped);
            } else if (activeResizer === "ai") {
                if (!workspaceRef.current) return;
                const rect = workspaceRef.current.getBoundingClientRect();
                const newWidth = rect.right - event.clientX;
                const minWidth = 260;
                const maxWidth = Math.max(minWidth, Math.min(800, rect.width - 400));
                const clamped = Math.max(minWidth, Math.min(maxWidth, newWidth));
                setAiPanelWidth(clamped);
            } else if (activeResizer === "terminal") {
                if (!centerRef.current) return;
                const rect = centerRef.current.getBoundingClientRect();
                const newHeight = rect.bottom - event.clientY;
                const minHeight = 80;
                const maxHeight = Math.max(minHeight, Math.min(650, rect.height - 100));
                const clamped = Math.max(minHeight, Math.min(maxHeight, newHeight));
                setTerminalHeight(clamped);
            }
        }

        function handleMouseUp() {
            setActiveResizer(null);
        }

        window.addEventListener("mousemove", handleMouseMove);
        window.addEventListener("mouseup", handleMouseUp);

        return () => {
            window.removeEventListener("mousemove", handleMouseMove);
            window.removeEventListener("mouseup", handleMouseUp);
        };
    }, [activeResizer]);


    // =========================================
    // Numeric Project ID
    // =========================================

    const numericProjectId =
        projectId ? Number(projectId) : 0;


    // =========================================
    // Active Tab
    // =========================================

    const activeTab =
        tabs.find(
            tab =>
                tab.file.id === activeTabId
        ) ?? null;


    // =========================================
    // Back to Projects
    // =========================================

    function handleBack() {

        const hasUnsavedChanges =
            tabs.some(
                tab =>
                    tab.isDirty
            );


        if (hasUnsavedChanges) {

            const confirmed =
                window.confirm(
                    "You have unsaved changes. Leave the IDE?"
                );


            if (!confirmed) {

                return;
            }
        }


        navigate("/projects");
    }


    // =========================================
    // Open File
    // =========================================

    async function openFile(
        file: FileTreeNode
    ) {

        // Folders cannot be opened

        if (file.type !== "file") {

            return;
        }


        console.log(
            "Opening file:",
            file
        );


        // =====================================
        // Already Open
        // =====================================

        const existingTab =
            tabs.find(
                tab =>
                    tab.file.id === file.id
            );


        if (existingTab) {

            console.log(
                "File already open. Switching tab:",
                file.name
            );


            setActiveTabId(
                file.id
            );


            setSaveError("");


            return;
        }


        // =====================================
        // Load File
        // =====================================

        try {

            const files =
                await getProjectFiles(
                    numericProjectId
                );


            const selected =
                files.find(
                    item =>
                        item.id === file.id
                );


            if (!selected) {

                console.error(
                    "File not found:",
                    file.id
                );

                return;
            }


            const newTab: EditorTab = {

                file: file,

                content:
                    selected.content ?? "",

                language:
                    getLanguageFromFileName(
                        selected.name
                    ),

                isDirty: false,
            };


            // Add new tab

            setTabs(
                currentTabs => [
                    ...currentTabs,
                    newTab,
                ]
            );


            // Make new tab active

            setActiveTabId(
                file.id
            );


            setSaveError("");


            console.log(
                "File opened:",
                selected.name
            );

        } catch (error) {

            console.error(
                "Failed to open file:",
                error
            );

        }
    }


    // =========================================
    // Update Active Tab Content
    // =========================================

    function handleEditorChange(
        value: string
    ) {

        if (activeTabId === null) {

            return;
        }


        setTabs(
            currentTabs =>
                currentTabs.map(
                    tab => {

                        if (
                            tab.file.id !==
                            activeTabId
                        ) {

                            return tab;
                        }


                        return {

                            ...tab,

                            content: value,

                            isDirty: true,

                        };

                    }
                )
        );


        setSaveError("");
    }
    // =========================================
    // Apply AI Generated Code
    // =========================================

    function handleApplyCode(
        code: string
    ) {
        if (activeTabId === null) {
            return;
        }

        setTabs(
            currentTabs =>
                currentTabs.map(
                    tab => {

                        if (
                            tab.file.id !== activeTabId
                        ) {
                            return tab;
                        }

                        return {
                            ...tab,
                            content: code,
                            isDirty: true,
                        };
                    }
                )
        );

        setSaveError("");
    }

    // =========================================
    // Switch Tab
    // =========================================

    function handleTabClick(
        fileId: number
    ) {

        console.log(
            "Switching tab:",
            fileId
        );


        setActiveTabId(
            fileId
        );


        setSaveError("");
    }


    // =========================================
    // Close Tab
    // =========================================

    function handleCloseTab(
        fileId: number
    ) {

        const tabToClose =
            tabs.find(
                tab =>
                    tab.file.id === fileId
            );


        if (!tabToClose) {

            return;
        }


        // =====================================
        // Check Unsaved Changes
        // =====================================

        if (tabToClose.isDirty) {

            const confirmed =
                window.confirm(
                    `"${tabToClose.file.name}" has unsaved changes. Close anyway?`
                );


            if (!confirmed) {

                return;
            }
        }


        const tabIndex =
            tabs.findIndex(
                tab =>
                    tab.file.id === fileId
            );


        const remainingTabs =
            tabs.filter(
                tab =>
                    tab.file.id !== fileId
            );


        setTabs(
            remainingTabs
        );


        // =====================================
        // Closing Active Tab
        // =====================================

        if (
            activeTabId === fileId
        ) {

            if (
                remainingTabs.length === 0
            ) {

                setActiveTabId(null);

                setSaveError("");

                return;
            }


            // Prefer the tab to the left

            const previousTabIndex =
                Math.max(
                    0,
                    tabIndex - 1
                );


            const nextActiveTab =
                remainingTabs[
                previousTabIndex
                ];


            setActiveTabId(
                nextActiveTab.file.id
            );


            setSaveError("");
        }
    }


    // =========================================
    // Save Active File
    // =========================================

    async function saveFile() {

        if (!activeTab) {

            console.log(
                "No active file to save."
            );

            return;
        }


        if (
            activeTab.file.type !==
            "file"
        ) {

            return;
        }


        if (!activeTab.isDirty) {

            console.log(
                "No changes to save."
            );

            return;
        }


        console.log(
            "================================="
        );


        console.log(
            "Saving active file..."
        );


        console.log(
            "Project ID:",
            numericProjectId
        );


        console.log(
            "File ID:",
            activeTab.file.id
        );


        console.log(
            "File Name:",
            activeTab.file.name
        );


        console.log(
            "Content:",
            activeTab.content
        );


        console.log(
            "================================="
        );


        setIsSaving(true);

        setSaveError("");


        try {

            const updatedFile =
                await updateProjectFile(
                    numericProjectId,
                    activeTab.file.id,
                    {
                        content:
                            activeTab.content,
                    }
                );


            console.log(
                "FILE SAVED SUCCESSFULLY:",
                updatedFile
            );


            // Mark only active tab as saved

            setTabs(
                currentTabs =>
                    currentTabs.map(
                        tab => {

                            if (
                                tab.file.id !==
                                activeTab.file.id
                            ) {

                                return tab;
                            }


                            return {

                                ...tab,

                                isDirty: false,

                            };

                        }
                    )
            );


            setSaveError("");


        } catch (error: any) {

            console.error(
                "FAILED TO SAVE FILE:",
                error
            );


            console.error(
                "Save response:",
                error.response
            );


            setSaveError(
                error.response?.data?.detail ??
                "Failed to save file."
            );


        } finally {

            setIsSaving(false);
        }
    }


    // =========================================
    // Ctrl + S
    // =========================================

    useEffect(() => {

        function handleKeyboardShortcut(
            event: KeyboardEvent
        ) {

            if (
                (event.ctrlKey || event.metaKey) &&
                event.key.toLowerCase() === "s"
            ) {

                event.preventDefault();


                console.log(
                    "Ctrl + S detected"
                );


                saveFile();
            }
        }


        window.addEventListener(
            "keydown",
            handleKeyboardShortcut
        );


        return () => {

            window.removeEventListener(
                "keydown",
                handleKeyboardShortcut
            );

        };

    }, [
        activeTab,
    ]);


    // =========================================
    // Create File
    // =========================================

    async function handleCreateFile(
        parentId: number | null
    ) {

        console.log(
            "IDEPage.handleCreateFile called"
        );


        console.log(
            "Project ID:",
            numericProjectId
        );


        console.log(
            "Parent ID:",
            parentId
        );


        const name =
            window.prompt(
                "Enter file name:"
            );


        console.log(
            "File name entered:",
            name
        );


        if (!name?.trim()) {

            console.log(
                "File creation cancelled"
            );

            return;
        }


        const data: ProjectFileCreate = {

            name:
                name.trim(),

            type:
                "file",

            parent_id:
                parentId,

            content:
                "",
        };


        console.log(
            "Sending create file request:",
            data
        );


        try {

            const result =
                await createProjectFile(
                    numericProjectId,
                    data
                );


            console.log(
                "CREATE FILE SUCCESS:",
                result
            );


            setRefreshKey(
                current =>
                    current + 1
            );

        } catch (error: any) {

            console.error(
                "CREATE FILE FAILED:",
                error
            );


            console.error(
                "Response:",
                error.response
            );


            window.alert(
                error.response?.data?.detail ??
                "Failed to create file."
            );
        }
    }


    // =========================================
    // Create Folder
    // =========================================

    async function handleCreateFolder(
        parentId: number | null
    ) {

        const name =
            window.prompt(
                "Enter folder name:"
            );


        if (!name?.trim()) {

            return;
        }


        const data: ProjectFileCreate = {

            name:
                name.trim(),

            type:
                "folder",

            parent_id:
                parentId,
        };


        try {

            await createProjectFile(
                numericProjectId,
                data
            );


            setRefreshKey(
                current =>
                    current + 1
            );

        } catch (error: any) {

            console.error(
                "Failed to create folder:",
                error
            );


            window.alert(
                error.response?.data?.detail ??
                "Failed to create folder."
            );
        }
    }


    // =========================================
    // Rename File / Folder
    // =========================================

    async function handleRename(
        node: FileTreeNode
    ) {

        const newName =
            window.prompt(
                "Enter new name:",
                node.name
            );


        if (
            !newName?.trim() ||
            newName.trim() === node.name
        ) {

            return;
        }


        try {

            await updateProjectFile(
                numericProjectId,
                node.id,
                {
                    name:
                        newName.trim(),
                }
            );


            // Update open tab

            setTabs(
                currentTabs =>
                    currentTabs.map(
                        tab => {

                            if (
                                tab.file.id !==
                                node.id
                            ) {

                                return tab;
                            }


                            return {

                                ...tab,

                                file: {

                                    ...tab.file,

                                    name:
                                        newName.trim(),

                                },

                                language:
                                    getLanguageFromFileName(
                                        newName.trim()
                                    ),

                            };

                        }
                    )
            );


            setRefreshKey(
                current =>
                    current + 1
            );

        } catch (error: any) {

            console.error(
                "Failed to rename:",
                error
            );


            window.alert(
                error.response?.data?.detail ??
                "Failed to rename."
            );
        }
    }


    // =========================================
    // Delete File / Folder
    // =========================================

    async function handleDelete(
        node: FileTreeNode
    ): Promise<void> {

        const confirmed =
            window.confirm(
                `Delete "${node.name}"?`
            );


        if (!confirmed) {

            return;
        }


        try {

            await deleteProjectFile(
                numericProjectId,
                node.id
            );


            // Find the tab that is being deleted

            const deletedTab =
                tabs.find(
                    tab =>
                        tab.file.id === node.id
                );


            // Remove deleted file from tabs

            const remainingTabs =
                tabs.filter(
                    tab =>
                        tab.file.id !== node.id
                );


            setTabs(
                remainingTabs
            );


            // =====================================
            // Deleted Active Tab
            // =====================================

            if (
                activeTabId === node.id
            ) {

                if (
                    remainingTabs.length === 0
                ) {

                    // No tabs remaining

                    setActiveTabId(null);

                } else {

                    // Find the position of the deleted tab

                    const deletedIndex =
                        tabs.findIndex(
                            tab =>
                                tab.file.id === node.id
                        );


                    // Prefer the tab on the left

                    const newIndex =
                        Math.max(
                            0,
                            deletedIndex - 1
                        );


                    const nextTab =
                        remainingTabs[newIndex] ??
                        remainingTabs[0];


                    setActiveTabId(
                        nextTab.file.id
                    );
                }
            }


            if (deletedTab) {

                setSaveError("");
            }


            setRefreshKey(
                current =>
                    current + 1
            );


        } catch (error: any) {

            console.error(
                "Failed to delete:",
                error
            );


            window.alert(
                error.response?.data?.detail ??
                "Failed to delete."
            );
        }
    }


    // =========================================
    // UI
    // =========================================

    if (!projectId) {
        return (
            <div className="ide-page" style={{ padding: 20, color: "#fff" }}>
                Invalid project.
            </div>
        );
    }

    return (

        <div className="ide-page">

            {/* ================================= */}
            {/* Header */}
            {/* ================================= */}

            <header className="ide-header">

                <div className="ide-header-left">

                    <button
                        type="button"
                        className="ide-back-button"
                        onClick={handleBack}
                        title="Back to projects"
                    >

                        <ArrowLeft
                            size={18}
                        />

                    </button>


                    <Code2
                        size={20}
                    />


                    <span>
                        AI Coding IDE
                    </span>

                </div>


                <div className="ide-project-info">

                    Project #{projectId}

                </div>

            </header>


            {/* ================================= */}
            {/* Main IDE Area */}
            {/* ================================= */}

            <div className="ide-main-area">

                {/* ================================= */}
                {/* Main Workspace */}
                {/* ================================= */}

                <div
                    className="ide-workspace"
                    ref={workspaceRef}
                >

                    {/* ================================= */}
                    {/* LEFT: FILE EXPLORER */}
                    {/* ================================= */}

                    <aside
                        className="ide-explorer"
                        style={{
                            width: `${explorerWidth}px`,
                            flexBasis: `${explorerWidth}px`,
                        }}
                    >

                        <FileExplorer
                            key={refreshKey}
                            projectId={
                                numericProjectId
                            }
                            onFileSelect={
                                openFile
                            }
                            onCreateFile={
                                handleCreateFile
                            }
                            onCreateFolder={
                                handleCreateFolder
                            }
                            onRename={
                                handleRename
                            }
                            onDelete={
                                handleDelete
                            }
                        />

                    </aside>


                    {/* ================================= */}
                    {/* RESIZER: EXPLORER / CENTER */}
                    {/* ================================= */}

                    <div
                        className={`ide-resizer ide-resizer-col ${activeResizer === "explorer"
                            ? "is-active"
                            : ""
                            }`}
                        onMouseDown={event => {
                            event.preventDefault();
                            setActiveResizer(
                                "explorer"
                            );
                        }}
                        onDoubleClick={() =>
                            setExplorerWidth(240)
                        }
                        title="Drag to resize explorer (Double-click to reset)"
                    >

                        <div className="ide-resizer-handle" />

                    </div>


                    {/* ================================= */}
                    {/* CENTER: EDITOR + TERMINAL */}
                    {/* ================================= */}

                    <div
                        className="ide-center"
                        ref={centerRef}
                    >

                        {/* ================================= */}
                        {/* EDITOR */}
                        {/* ================================= */}

                        <main className="ide-editor">

                            {tabs.length > 0 ? (

                                <>

                                    {/* ========================= */}
                                    {/* Editor Tabs */}
                                    {/* ========================= */}

                                    <div className="editor-tabs">

                                        {tabs.map(
                                            tab => (

                                                <div
                                                    key={
                                                        tab.file.id
                                                    }
                                                    className={
                                                        tab.file.id ===
                                                            activeTabId
                                                            ? "editor-tab active"
                                                            : "editor-tab"
                                                    }
                                                    onClick={() =>
                                                        handleTabClick(
                                                            tab.file.id
                                                        )
                                                    }
                                                >

                                                    <div
                                                        className="editor-tab-name"
                                                    >

                                                        {tab.isDirty && (

                                                            <span
                                                                className="editor-tab-dirty"
                                                            >
                                                                ●
                                                            </span>

                                                        )}


                                                        <span>
                                                            {
                                                                tab.file.name
                                                            }
                                                        </span>

                                                    </div>


                                                    <button
                                                        type="button"
                                                        className="editor-tab-close"
                                                        onClick={(
                                                            event
                                                        ) => {

                                                            event.stopPropagation();

                                                            handleCloseTab(
                                                                tab.file.id
                                                            );

                                                        }}
                                                        title={
                                                            `Close ${tab.file.name}`
                                                        }
                                                    >

                                                        <X
                                                            size={14}
                                                        />

                                                    </button>

                                                </div>

                                            )
                                        )}

                                    </div>


                                    {/* ========================= */}
                                    {/* Editor Toolbar */}
                                    {/* ========================= */}

                                    {activeTab && (

                                        <div
                                            className="editor-toolbar"
                                        >

                                            <div className="editor-toolbar-left">

                                                <span>
                                                    {
                                                        activeTab.file.name
                                                    }
                                                </span>


                                                {activeTab.isDirty && (

                                                    <span
                                                        className="editor-modified"
                                                    >
                                                        Modified
                                                    </span>

                                                )}

                                            </div>


                                            <div className="editor-actions">

                                                {saveError && (

                                                    <span
                                                        className="editor-save-error"
                                                        title={saveError}
                                                    >
                                                        {saveError}
                                                    </span>

                                                )}


                                                {isSaving ? (

                                                    <span
                                                        className="editor-save-status"
                                                    >
                                                        Saving...
                                                    </span>

                                                ) : activeTab.isDirty ? (

                                                    <span
                                                        className="editor-save-status"
                                                    >
                                                        Unsaved
                                                    </span>

                                                ) : (

                                                    <span
                                                        className="editor-save-status"
                                                    >
                                                        Saved
                                                    </span>

                                                )}


                                                <button
                                                    type="button"
                                                    className="editor-save-button"
                                                    onClick={
                                                        saveFile
                                                    }
                                                    disabled={
                                                        isSaving ||
                                                        !activeTab.isDirty
                                                    }
                                                    title="Save file (Ctrl+S)"
                                                >

                                                    <Save
                                                        size={15}
                                                    />

                                                    <span>
                                                        Save
                                                    </span>

                                                </button>

                                            </div>

                                        </div>

                                    )}


                                    {/* ========================= */}
                                    {/* Monaco Editor */}
                                    {/* ========================= */}

                                    {activeTab && (

                                        <div
                                            className="editor-area"
                                        >

                                            <CodeEditor
                                                key={
                                                    activeTab.file.id
                                                }
                                                value={
                                                    activeTab.content
                                                }
                                                language={
                                                    activeTab.language
                                                }
                                                onChange={(
                                                    value
                                                ) => {

                                                    handleEditorChange(
                                                        value ?? ""
                                                    );

                                                }}
                                            />

                                        </div>

                                    )}

                                </>

                            ) : (

                                /* ================================= */
                                /* Welcome Screen */
                                /* ================================= */

                                <div className="editor-welcome">

                                    <h2>
                                        Welcome to your AI Coding IDE
                                    </h2>


                                    <p>
                                        Select a file from the
                                        Explorer to start coding.
                                    </p>

                                </div>

                            )}

                        </main>


                        {/* ================================= */}
                        {/* RESIZER: EDITOR / TERMINAL */}
                        {/* ================================= */}

                        <div
                            className={`ide-resizer ide-resizer-row ${activeResizer === "terminal"
                                ? "is-active"
                                : ""
                                }`}
                            onMouseDown={event => {
                                event.preventDefault();
                                setActiveResizer(
                                    "terminal"
                                );
                            }}
                            onDoubleClick={() =>
                                setTerminalHeight(240)
                            }
                            title="Drag to resize terminal (Double-click to reset)"
                        >

                            <div className="ide-resizer-handle" />

                        </div>


                        {/* ================================= */}
                        {/* TERMINAL */}
                        {/* ================================= */}

                        <section
                            className="ide-terminal"
                            style={{
                                height: `${terminalHeight}px`,
                                flexBasis: `${terminalHeight}px`,
                            }}
                        >

                            <Terminal
                                projectId={
                                    numericProjectId
                                }
                            />

                        </section>

                    </div>


                    {/* ================================= */}
                    {/* RESIZER: CENTER / AI PANEL */}
                    {/* ================================= */}

                    <div
                        className={`ide-resizer ide-resizer-col ${activeResizer === "ai"
                            ? "is-active"
                            : ""
                            }`}
                        onMouseDown={event => {
                            event.preventDefault();
                            setActiveResizer(
                                "ai"
                            );
                        }}
                        onDoubleClick={() =>
                            setAiPanelWidth(360)
                        }
                        title="Drag to resize AI panel (Double-click to reset)"
                    >

                        <div className="ide-resizer-handle" />

                    </div>


                    {/* ================================= */}
                    {/* RIGHT: AI ASSISTANT */}
                    {/* ================================= */}

                    <aside
                        className="ide-ai-panel"
                        style={{
                            width: `${aiPanelWidth}px`,
                            flexBasis: `${aiPanelWidth}px`,
                        }}
                    >
                        <AIChat
                            context={activeTab?.content ?? null}
                            fileName={activeTab?.file.name ?? null}
                            onApplyCode={handleApplyCode}
                        />

                    </aside>


                    {/* ================================= */}
                    {/* Dragging Overlay */}
                    {/* ================================= */}

                    {activeResizer && (

                        <div
                            className={`ide-resize-overlay ${activeResizer ===
                                "terminal"
                                ? "row-resizing"
                                : "col-resizing"
                                }`}
                        />

                    )}

                </div>

            </div>

        </div>

    );
}