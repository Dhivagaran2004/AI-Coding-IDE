import {
    useEffect,
    useState,
    useRef,
    type FormEvent,
} from "react";

import CodeEditor, {
    type CodeEditorHandle,
} from "../components/editor/CodeEditor";

import Terminal, {
    type TerminalHandle,
} from "../components/terminal/Terminal";

import {
    getProjectFileTree,
    getProjectFiles,
    createProjectFile,
    updateProjectFile,
    deleteProjectFile,
    applyProjectFileCodeAction,
} from "../services/fileService";

import {
    getLanguageFromFileName,
} from "../utils/language";

import {
    useNavigate,
    useParams,
} from "react-router-dom";
import { createPortal } from "react-dom";
import { useAuth } from "../context/AuthContext";

import {
    Code2,
    FilePlus,
    FolderPlus,
    GitBranch,
    Pencil,
    Save,
    X,
} from "lucide-react";

import FileExplorer from
    "../components/explorer/FileExplorer";
import IDEActivityBar, {
    type IDEActivityView,
} from "../components/ide/IDEActivityBar";
import ProjectSearchPanel from "../components/ide/ProjectSearchPanel";
import IDEMenuBar, {
    type IDEMenu,
} from "../components/ide/IDEMenuBar";

import type {
    FileTreeNode,
    ProjectFileCreate,
} from "../types/file";

import AIChat, { type AIChatHandle } from "../components/ai/AIChat";
import type { TerminalAIContext } from "../services/terminalService";
import type { CodeAction } from "../services/aiService";
import { getGitDiff, getGitStatus, type GitStatus } from "../services/gitService";

// =========================================
// Editor Tab Type
// =========================================

interface EditorTab {

    file: FileTreeNode;

    content: string;

    language: string;

    isDirty: boolean;
}

type NameDialog =
    | {
        kind: "file";
        parentId: number | null;
    }
    | {
        kind: "folder";
        parentId: number | null;
    }
    | {
        kind: "rename";
        node: FileTreeNode;
    };

interface TabContextMenuState {
    fileId: number;
    x: number;
    y: number;
}


// =========================================
// IDE Page
// =========================================

export default function IDEPage() {

    const navigate = useNavigate();
    const { logout } = useAuth();


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

    const [selectedCode, setSelectedCode] = useState<{
        code: string;
        startLine: number;
        endLine: number;
    } | null>(null);

    const [terminalContext, setTerminalContext] =
        useState<TerminalAIContext | null>(null);
    const [gitStatus, setGitStatus] = useState<GitStatus | null>(null);
    const [gitDiff, setGitDiff] = useState<string | null>(null);
    const [gitDiffStaged, setGitDiffStaged] = useState(false);
    const [gitDiffTruncated, setGitDiffTruncated] = useState(false);
    const [gitError, setGitError] = useState("");
    const [nameDialog, setNameDialog] = useState<NameDialog | null>(null);
    const [nameDialogValue, setNameDialogValue] = useState("");
    const [activeSidebar, setActiveSidebar] =
        useState<IDEActivityView | null>("explorer");
    const [isTerminalVisible, setIsTerminalVisible] = useState(true);
    const [isChatVisible, setIsChatVisible] = useState(true);
    const [isShortcutHelpOpen, setIsShortcutHelpOpen] = useState(false);
    const [tabContextMenu, setTabContextMenu] =
        useState<TabContextMenuState | null>(null);
    const [pendingCloseTabIds, setPendingCloseTabIds] =
        useState<number[] | null>(null);

    useEffect(() => {
        setSelectedCode(null);
    }, [activeTabId]);

    useEffect(() => {
        if (!tabContextMenu) {
            return;
        }

        const handlePointerDown = (event: PointerEvent) => {
            if (
                event.target instanceof Element &&
                event.target.closest("[data-editor-tab-menu]")
            ) {
                return;
            }
            setTabContextMenu(null);
        };
        const handleKeyDown = (event: KeyboardEvent) => {
            if (event.key === "Escape") {
                setTabContextMenu(null);
            }
        };

        document.addEventListener("pointerdown", handlePointerDown);
        document.addEventListener("keydown", handleKeyDown);
        return () => {
            document.removeEventListener("pointerdown", handlePointerDown);
            document.removeEventListener("keydown", handleKeyDown);
        };
    }, [tabContextMenu]);


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

    type AIUndoState = {
        id: string;
        tabId: string | number;
        previousContent: string;
        description: string;
        timestamp: number;
    };

    const [aiUndoHistory, setAiUndoHistory] =
        useState<AIUndoState[]>([]);
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
    const editorRef = useRef<CodeEditorHandle | null>(null);
    const terminalRef = useRef<TerminalHandle | null>(null);

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
                const newWidth = event.clientX - rect.left - 48;
                const minWidth = 160;
                const maxWidth = Math.max(minWidth, Math.min(600, rect.width - 498));
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
    const aiChatRef = useRef<AIChatHandle | null>(null);

    useEffect(() => {
        let active = true;
        const refreshStatus = () => {
            void getGitStatus(numericProjectId)
                .then((status) => {
                    if (active) {
                        setGitStatus(status);
                        setGitError("");
                    }
                })
                .catch(() => {
                    if (active) {
                        setGitStatus(null);
                        setGitError("Git status unavailable");
                    }
                });
        };
        refreshStatus();
        const interval = window.setInterval(refreshStatus, 10000);
        return () => {
            active = false;
            window.clearInterval(interval);
        };
    }, [numericProjectId]);

    const openGitDiff = async (staged: boolean) => {
        try {
            const result = await getGitDiff(numericProjectId, staged);
            setGitDiff(result.diff);
            setGitDiffTruncated(result.truncated);
            setGitDiffStaged(staged);
        } catch {
            setGitDiff("Unable to load Git diff.");
            setGitDiffTruncated(false);
            setGitDiffStaged(staged);
        }
    };

    function handleActivitySelect(view: IDEActivityView) {
        setActiveSidebar((current) => current === view ? null : view);
    }

    function resetLayout() {
        setExplorerWidth(240);
        setAiPanelWidth(360);
        setTerminalHeight(240);
        setActiveSidebar("explorer");
    }

    function handleLogout() {
        logout();
        navigate("/login");
    }


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
        code: string,
        description = "AI code change",
    ) {
        if (activeTabId === null) {
            return;
        }

        const activeTab = tabs.find(
            (tab) =>
                tab.file.id ===
                activeTabId,
        );

        if (!activeTab) {
            return;
        }

        /*
         * Save the current editor content
         * before applying the AI change.
         *
         * Each AI application creates a new
         * undo history entry.
         */
        setAiUndoHistory(
            (previousHistory) => [
                ...previousHistory,
                {
                    id: `${Date.now()}-${Math.random()}`,
                    tabId: activeTabId,
                    previousContent:
                        activeTab.content,
                    description,
                    timestamp: Date.now(),
                },
            ],
        );

        /*
         * Apply AI-generated content.
         */
        setTabs(
            (currentTabs) =>
                currentTabs.map(
                    (tab) => {
                        if (
                            tab.file.id !==
                            activeTabId
                        ) {
                            return tab;
                        }

                        return {
                            ...tab,
                            content: code,
                            isDirty: true,
                        };
                    },
                ),
        );

        setSaveError("");
    }

    function handleUndoAIChange() {
        if (activeTabId === null) {
            return;
        }

        /*
         * Find the most recent AI change
         * belonging to the currently active file.
         */
        const historyIndex =
            [...aiUndoHistory]
                .reverse()
                .findIndex(
                    (entry) =>
                        entry.tabId ===
                        activeTabId,
                );

        if (historyIndex === -1) {
            return;
        }

        /*
         * Convert the reversed index back
         * to the original array index.
         */
        const actualIndex =
            aiUndoHistory.length -
            1 -
            historyIndex;

        const historyEntry =
            aiUndoHistory[
            actualIndex
            ];

        /*
         * Restore the previous editor content.
         */
        setTabs(
            (currentTabs) =>
                currentTabs.map(
                    (tab) => {
                        if (
                            tab.file.id !==
                            activeTabId
                        ) {
                            return tab;
                        }

                        return {
                            ...tab,
                            content:
                                historyEntry.previousContent,
                            isDirty: true,
                        };
                    },
                ),
        );

        /*
         * Remove only the history entry
         * that we just consumed.
         */
        setAiUndoHistory(
            (previousHistory) =>
                previousHistory.filter(
                    (_, index) =>
                        index !==
                        actualIndex,
                ),
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

    function closeTabsImmediately(fileIds: number[]) {
        const closingIds = new Set(fileIds);
        const activeIndex = tabs.findIndex((tab) => tab.file.id === activeTabId);
        const remainingTabs = tabs.filter((tab) => !closingIds.has(tab.file.id));

        setTabs(remainingTabs);
        if (activeTabId !== null && closingIds.has(activeTabId)) {
            const previousTab = tabs
                .slice(0, activeIndex)
                .reverse()
                .find((tab) => !closingIds.has(tab.file.id));
            const nextTab = tabs
                .slice(activeIndex + 1)
                .find((tab) => !closingIds.has(tab.file.id));
            setActiveTabId(previousTab?.file.id ?? nextTab?.file.id ?? null);
            setSaveError("");
        }
    }

    function handleCloseTabs(fileIds: number[]) {
        const idsToClose = [...new Set(fileIds)].filter((id) =>
            tabs.some((tab) => tab.file.id === id),
        );
        if (idsToClose.length === 0) {
            return;
        }

        const hasUnsavedChanges = tabs.some(
            (tab) => idsToClose.includes(tab.file.id) && tab.isDirty,
        );
        if (hasUnsavedChanges) {
            setPendingCloseTabIds(idsToClose);
            setTabContextMenu(null);
            return;
        }
        closeTabsImmediately(idsToClose);
        setTabContextMenu(null);
    }

    function handleCloseTab(fileId: number) {
        handleCloseTabs([fileId]);
    }


    // =========================================
    // Save Active File
    // =========================================

    async function handleApplyPatch(action: CodeAction) {
        const openTarget = tabs.find(
            (tab) => tab.file.path === action.file_path || tab.file.name === action.file_path,
        );
        let targetFile = openTarget?.file;

        if (!targetFile) {
            const pending = [...await getProjectFileTree(numericProjectId)];
            const projectFiles: FileTreeNode[] = [];
            while (pending.length > 0) {
                const file = pending.pop();
                if (!file) {
                    continue;
                }
                if (file.type === "file") {
                    projectFiles.push(file);
                }
                pending.push(...(file.children ?? []));
            }

            targetFile = projectFiles.find((file) => file.path === action.file_path);
            if (!targetFile && !action.file_path.includes("/")) {
                const matchingNames = projectFiles.filter((file) => file.name === action.file_path);
                if (matchingNames.length === 1) {
                    targetFile = matchingNames[0];
                }
            }
        }

        if (!targetFile) {
            throw new Error(`Could not find the proposed file: ${action.file_path}`);
        }

        const targetTab = tabs.find((tab) => tab.file.id === targetFile.id);
        if (targetTab?.isDirty) {
            throw new Error("Save or discard changes in the proposed file before applying this patch.");
        }

        const updatedFile = await applyProjectFileCodeAction(
            numericProjectId,
            targetFile.id,
            action,
        );

        setTabs((currentTabs) => {
            const existingTab = currentTabs.find((tab) => tab.file.id === targetFile.id);
            const updatedTab: EditorTab = {
                file: targetFile,
                content: updatedFile.content ?? "",
                language: existingTab?.language ?? getLanguageFromFileName(targetFile.name),
                isDirty: false,
            };
            return existingTab
                ? currentTabs.map((tab) => tab.file.id === targetFile.id ? updatedTab : tab)
                : [...currentTabs, updatedTab];
        });
        setActiveTabId(targetFile.id);
        setSaveError("");
        setRefreshKey((current) => current + 1);
    }

    function handleAgentChangesApplied(
        changes: {
            path: string;
            content: string;
            file_id: number;
            deleted?: boolean;
        }[],
    ) {
        if (changes.length === 0) {
            return;
        }

        const deletedIds = new Set(
            changes.filter((change) => change.deleted).map((change) => change.file_id),
        );
        setTabs((currentTabs) => {
            const updatedTabs = currentTabs.filter(
                (tab) => !deletedIds.has(tab.file.id),
            );
            for (const change of changes) {
                if (change.deleted) {
                    continue;
                }
                const name = change.path.split("/").pop() ?? change.path;
                const existingIndex = updatedTabs.findIndex(
                    (tab) => tab.file.id === change.file_id,
                );
                const existingTab = existingIndex >= 0
                    ? updatedTabs[existingIndex]
                    : undefined;
                const updatedTab: EditorTab = {
                    file: existingTab?.file ?? {
                        id: change.file_id,
                        name,
                        type: "file",
                        parent_id: null,
                        path: change.path,
                    },
                    content: change.content,
                    language: existingTab?.language ?? getLanguageFromFileName(name),
                    isDirty: false,
                };

                if (existingIndex >= 0) {
                    updatedTabs[existingIndex] = updatedTab;
                } else {
                    updatedTabs.push(updatedTab);
                }
            }
            return updatedTabs;
        });
        const latestNonDeleted = [...changes]
            .reverse()
            .find((change) => !change.deleted);
        const fallbackActiveId = tabs
            .filter((tab) => !deletedIds.has(tab.file.id))
            .at(-1)?.file.id ?? null;
        setActiveTabId((currentId) => {
            if (currentId !== null && deletedIds.has(currentId)) {
                return latestNonDeleted?.file_id ?? fallbackActiveId;
            }
            return latestNonDeleted?.file_id ?? currentId;
        });
        setRefreshKey((current) => current + 1);
        setSaveError("");
    }

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

        const fileIdToSave = activeTab.file.id;
        const contentToSave = activeTab.content;

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
                    fileIdToSave,
                    {
                        content:
                            contentToSave,
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
                                fileIdToSave
                            ) {

                                return tab;
                            }


                            return {

                                ...tab,

                                isDirty: tab.content !== contentToSave,

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
        parentId: number | null,
        requestedName?: string
    ) {

        if (requestedName === undefined) {
            setNameDialog({ kind: "file", parentId });
            setNameDialogValue("");
            return;
        }

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


        const name = requestedName;

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
        parentId: number | null,
        requestedName?: string
    ) {

        if (requestedName === undefined) {
            setNameDialog({ kind: "folder", parentId });
            setNameDialogValue("");
            return;
        }

        if (!requestedName.trim()) {

            return;
        }


        const data: ProjectFileCreate = {

            name:
                requestedName.trim(),

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
        node: FileTreeNode,
        requestedName?: string
    ) {

        if (requestedName === undefined) {
            setNameDialog({ kind: "rename", node });
            setNameDialogValue(node.name);
            return;
        }

        const newName = requestedName;

        if (
            !newName.trim() ||
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

    function handleNameDialogSubmit(event: FormEvent<HTMLFormElement>) {
        event.preventDefault();
        const dialog = nameDialog;
        const name = nameDialogValue.trim();

        if (!dialog || !name) {
            return;
        }

        setNameDialog(null);

        if (dialog.kind === "file") {
            void handleCreateFile(dialog.parentId, name);
        } else if (dialog.kind === "folder") {
            void handleCreateFolder(dialog.parentId, name);
        } else {
            void handleRename(dialog.node, name);
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

    function focusTerminal() {
        setIsTerminalVisible(true);
        window.setTimeout(() => terminalRef.current?.focus(), 0);
    }

    const menuDefinitions: IDEMenu[] = [
        {
            label: "File",
            items: [
                { label: "New File", onSelect: () => void handleCreateFile(null) },
                { label: "New Folder", onSelect: () => void handleCreateFolder(null) },
                {
                    label: "Save",
                    shortcut: "Ctrl+S",
                    disabled: !activeTab?.isDirty || isSaving,
                    onSelect: () => void saveFile(),
                },
                {
                    label: "Close Active Tab",
                    disabled: activeTabId === null,
                    onSelect: () => {
                        if (activeTabId !== null) {
                            handleCloseTab(activeTabId);
                        }
                    },
                },
                {
                    label: "Close Other Tabs",
                    disabled: activeTabId === null || tabs.length < 2,
                    onSelect: () =>
                        handleCloseTabs(
                            tabs
                                .filter((tab) => tab.file.id !== activeTabId)
                                .map((tab) => tab.file.id),
                        ),
                },
                {
                    label: "Close All Tabs",
                    disabled: tabs.length === 0,
                    onSelect: () =>
                        handleCloseTabs(tabs.map((tab) => tab.file.id)),
                },
                { label: "Back to Projects", onSelect: handleBack },
            ],
        },
        {
            label: "Edit",
            items: [
                {
                    label: "Undo",
                    shortcut: "Ctrl+Z",
                    disabled: !activeTab,
                    onSelect: () => editorRef.current?.runAction("undo"),
                },
                {
                    label: "Redo",
                    shortcut: "Ctrl+Y",
                    disabled: !activeTab,
                    onSelect: () => editorRef.current?.runAction("redo"),
                },
                {
                    label: "Find",
                    shortcut: "Ctrl+F",
                    disabled: !activeTab,
                    onSelect: () => editorRef.current?.runAction("find"),
                },
                {
                    label: "Select All",
                    shortcut: "Ctrl+A",
                    disabled: !activeTab,
                    onSelect: () => editorRef.current?.runAction("selectAll"),
                },
                {
                    label: "Undo AI Change",
                    disabled: !aiUndoHistory.some((entry) => entry.tabId === activeTabId),
                    onSelect: handleUndoAIChange,
                },
            ],
        },
        {
            label: "Selection",
            items: [
                {
                    label: "Select All",
                    shortcut: "Ctrl+A",
                    disabled: !activeTab,
                    onSelect: () => editorRef.current?.runAction("selectAll"),
                },
                {
                    label: "Search Workspace",
                    onSelect: () => setActiveSidebar("search"),
                },
            ],
        },
        {
            label: "View",
            items: [
                { label: "Explorer", onSelect: () => setActiveSidebar("explorer") },
                { label: "Search", onSelect: () => setActiveSidebar("search") },
                {
                    label: "Source Control",
                    onSelect: () => setActiveSidebar("source-control"),
                },
                {
                    label: "Toggle Sidebar",
                    shortcut: "Ctrl+B",
                    onSelect: () =>
                        setActiveSidebar((current) => current ? null : "explorer"),
                },
                {
                    label: "Toggle Terminal",
                    shortcut: "Ctrl+`",
                    onSelect: () => setIsTerminalVisible((visible) => !visible),
                },
                {
                    label: "Toggle AI Chat",
                    onSelect: () => setIsChatVisible((visible) => !visible),
                },
            ],
        },
        {
            label: "Go",
            items: [
                { label: "Back to Projects", onSelect: handleBack },
                { label: "Open Explorer", onSelect: () => setActiveSidebar("explorer") },
                { label: "Open Search", onSelect: () => setActiveSidebar("search") },
            ],
        },
        {
            label: "Run",
            items: [
                {
                    label: "Run and Debug",
                    onSelect: () => setActiveSidebar("run-debug"),
                },
                { label: "Focus Terminal", onSelect: focusTerminal },
            ],
        },
        {
            label: "Terminal",
            items: [
                { label: "Focus Terminal", onSelect: focusTerminal },
                {
                    label: "Toggle Integrated Terminal",
                    onSelect: () => setIsTerminalVisible((visible) => !visible),
                },
            ],
        },
        {
            label: "Help",
            items: [
                {
                    label: "Keyboard Shortcuts",
                    onSelect: () => setIsShortcutHelpOpen(true),
                },
            ],
        },
    ];

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
                    <IDEMenuBar menus={menuDefinitions} />

                </div>


                <div className="ide-project-info">
                    Project #{projectId}
                </div>

            </header>

            {gitDiff !== null && (
                <div
                    className="ide-git-overlay"
                    role="presentation"
                    onMouseDown={(event) => {
                        if (event.target === event.currentTarget) {
                            setGitDiff(null);
                        }
                    }}
                >
                    <section
                        className="ide-git-dialog"
                        role="dialog"
                        aria-modal="true"
                        aria-label="Git status and diff"
                    >
                        <header className="ide-git-dialog__header">
                            <div>
                                <strong>Git changes</strong>
                                <span>{gitStatus?.branch || "Branch unavailable"}</span>
                            </div>
                            <button type="button" onClick={() => setGitDiff(null)} aria-label="Close Git diff">×</button>
                        </header>
                        <div className="ide-git-dialog__files">
                            {gitStatus?.changed_files.map((file) => (
                                <div key={file.path}>
                                    <code>{file.path}</code>
                                    <span>{file.status}</span>
                                </div>
                            ))}
                            {!gitStatus && <span>{gitError || "Loading Git status..."}</span>}
                        </div>
                        <div className="ide-git-dialog__tabs">
                            <button
                                type="button"
                                className={!gitDiffStaged ? "is-active" : ""}
                                onClick={() => void openGitDiff(false)}
                            >
                                Working tree
                            </button>
                            <button
                                type="button"
                                className={gitDiffStaged ? "is-active" : ""}
                                onClick={() => void openGitDiff(true)}
                            >
                                Staged
                            </button>
                        </div>
                        {gitDiffTruncated && <p className="ide-git-dialog__notice">Diff truncated to keep the IDE responsive.</p>}
                        <pre className="ide-git-dialog__diff">{gitDiff || "No diff available."}</pre>
                    </section>
                </div>
            )}

            {nameDialog && (
                <div
                    className="ide-name-overlay"
                    role="presentation"
                    onMouseDown={(event) => {
                        if (event.target === event.currentTarget) {
                            setNameDialog(null);
                        }
                    }}
                    onKeyDown={(event) => {
                        if (event.key === "Escape") {
                            setNameDialog(null);
                        }
                    }}
                >
                    <section
                        className="ide-name-dialog"
                        role="dialog"
                        aria-modal="true"
                        aria-labelledby="ide-name-dialog-title"
                    >
                        <header className="ide-name-dialog__header">
                            <span className="ide-name-dialog__icon">
                                {nameDialog.kind === "file" ? (
                                    <FilePlus size={18} />
                                ) : nameDialog.kind === "folder" ? (
                                    <FolderPlus size={18} />
                                ) : (
                                    <Pencil size={18} />
                                )}
                            </span>
                            <div>
                                <h2 id="ide-name-dialog-title">
                                    {nameDialog.kind === "file"
                                        ? "Create new file"
                                        : nameDialog.kind === "folder"
                                            ? "Create new folder"
                                            : "Rename item"}
                                </h2>
                                <p>
                                    {nameDialog.kind === "rename"
                                        ? "Choose a new name for this item."
                                        : "Give your project item a name to get started."}
                                </p>
                            </div>
                            <button
                                type="button"
                                className="ide-name-dialog__close"
                                onClick={() => setNameDialog(null)}
                                aria-label="Close dialog"
                            >
                                <X size={18} />
                            </button>
                        </header>
                        <form onSubmit={handleNameDialogSubmit}>
                            <label htmlFor="ide-name-input">
                                {nameDialog.kind === "rename"
                                    ? "New name"
                                    : nameDialog.kind === "folder"
                                        ? "Folder name"
                                        : "File name"}
                            </label>
                            <input
                                id="ide-name-input"
                                autoFocus
                                value={nameDialogValue}
                                onChange={(event) => setNameDialogValue(event.target.value)}
                                placeholder={
                                    nameDialog.kind === "folder"
                                        ? "e.g. components"
                                        : "e.g. App.tsx"
                                }
                            />
                            <footer className="ide-name-dialog__actions">
                                <button
                                    type="button"
                                    className="ide-name-dialog__cancel"
                                    onClick={() => setNameDialog(null)}
                                >
                                    Cancel
                                </button>
                                <button
                                    type="submit"
                                    className="ide-name-dialog__submit"
                                    disabled={!nameDialogValue.trim()}
                                >
                                    {nameDialog.kind === "rename" ? "Save name" : "Create"}
                                </button>
                            </footer>
                        </form>
                    </section>
                </div>
            )}

            {pendingCloseTabIds && (
                <div
                    className="ide-name-overlay"
                    role="presentation"
                    onMouseDown={(event) => {
                        if (event.target === event.currentTarget) {
                            setPendingCloseTabIds(null);
                        }
                    }}
                    onKeyDown={(event) => {
                        if (event.key === "Escape") {
                            setPendingCloseTabIds(null);
                        }
                    }}
                >
                    <section
                        className="ide-name-dialog"
                        role="alertdialog"
                        aria-modal="true"
                        aria-labelledby="ide-close-tabs-title"
                        tabIndex={-1}
                        autoFocus
                    >
                        <header className="ide-name-dialog__header">
                            <span className="ide-name-dialog__icon ide-name-dialog__icon--danger">
                                <X size={18} />
                            </span>
                            <div>
                                <h2 id="ide-close-tabs-title">
                                    Close tabs with unsaved changes?
                                </h2>
                                <p>
                                    Your unsaved edits will be discarded. Save any changes you want to keep first.
                                </p>
                            </div>
                        </header>
                        <ul className="ide-close-tabs-list">
                            {tabs
                                .filter(
                                    (tab) =>
                                        pendingCloseTabIds.includes(tab.file.id) &&
                                        tab.isDirty,
                                )
                                .map((tab) => (
                                    <li key={tab.file.id}>{tab.file.name}</li>
                                ))}
                        </ul>
                        <footer className="ide-name-dialog__actions">
                            <button
                                type="button"
                                className="ide-name-dialog__cancel"
                                onClick={() => setPendingCloseTabIds(null)}
                            >
                                Cancel
                            </button>
                            <button
                                type="button"
                                className="ide-name-dialog__submit ide-name-dialog__submit--danger"
                                onClick={() => {
                                    closeTabsImmediately(pendingCloseTabIds);
                                    setPendingCloseTabIds(null);
                                }}
                            >
                                Discard and close
                            </button>
                        </footer>
                    </section>
                </div>
            )}

            {tabContextMenu &&
                createPortal(
                    <div
                        className="editor-tab-context-menu"
                        data-editor-tab-menu
                        role="menu"
                        aria-label="Editor tab actions"
                        style={{
                            left: tabContextMenu.x,
                            top: tabContextMenu.y,
                        }}
                    >
                        <button
                            type="button"
                            role="menuitem"
                            onClick={() => handleCloseTab(tabContextMenu.fileId)}
                        >
                            Close Tab
                        </button>
                        <button
                            type="button"
                            role="menuitem"
                            disabled={tabs.length < 2}
                            onClick={() =>
                                handleCloseTabs(
                                    tabs
                                        .filter((tab) => tab.file.id !== tabContextMenu.fileId)
                                        .map((tab) => tab.file.id),
                                )
                            }
                        >
                            Close Others
                        </button>
                        <button
                            type="button"
                            role="menuitem"
                            onClick={() => handleCloseTabs(tabs.map((tab) => tab.file.id))}
                        >
                            Close All
                        </button>
                    </div>,
                    document.body,
                )}

            {isShortcutHelpOpen && (
                <div
                    className="ide-name-overlay"
                    role="presentation"
                    onMouseDown={(event) => {
                        if (event.target === event.currentTarget) {
                            setIsShortcutHelpOpen(false);
                        }
                    }}
                    onKeyDown={(event) => {
                        if (event.key === "Escape") {
                            setIsShortcutHelpOpen(false);
                        }
                    }}
                >
                    <section
                        className="ide-name-dialog ide-shortcut-dialog"
                        role="dialog"
                        aria-modal="true"
                        aria-labelledby="ide-shortcut-title"
                        tabIndex={-1}
                        autoFocus
                    >
                        <header className="ide-name-dialog__header">
                            <span className="ide-name-dialog__icon">
                                <Code2 size={18} />
                            </span>
                            <div>
                                <h2 id="ide-shortcut-title">Keyboard shortcuts</h2>
                                <p>Available while the editor is active.</p>
                            </div>
                            <button
                                type="button"
                                className="ide-name-dialog__close"
                                onClick={() => setIsShortcutHelpOpen(false)}
                                aria-label="Close keyboard shortcuts"
                            >
                                <X size={18} />
                            </button>
                        </header>
                        <div className="ide-shortcut-list">
                            {[
                                ["Save active file", "Ctrl+S"],
                                ["Undo", "Ctrl+Z"],
                                ["Redo", "Ctrl+Y"],
                                ["Find in active file", "Ctrl+F"],
                                ["Select all", "Ctrl+A"],
                            ].map(([label, shortcut]) => (
                                <div className="ide-shortcut-row" key={label}>
                                    <span>{label}</span>
                                    <kbd>{shortcut}</kbd>
                                </div>
                            ))}
                        </div>
                    </section>
                </div>
            )}

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

                    <IDEActivityBar
                        activeView={activeSidebar}
                        onSelect={handleActivitySelect}
                    />

                    {/* ================================= */}
                    {/* LEFT: FILE EXPLORER */}
                    {/* ================================= */}

                    <aside
                        className={`ide-explorer${activeSidebar ? "" : " is-collapsed"}`}
                        style={{
                            width: activeSidebar ? `${explorerWidth}px` : "0px",
                            flexBasis: activeSidebar ? `${explorerWidth}px` : "0px",
                            display: activeSidebar ? "block" : "none",
                        }}
                    >

                        <div
                            className="ide-sidebar-panel"
                            style={{
                                display: activeSidebar === "explorer" ? "block" : "none",
                            }}
                        >
                            <FileExplorer
                                projectId={numericProjectId}
                                refreshKey={refreshKey}
                                workspaceName={`Project #${projectId}`}
                                activeFileId={activeTabId}
                                onFileSelect={openFile}
                                onCreateFile={handleCreateFile}
                                onCreateFolder={handleCreateFolder}
                                onRename={handleRename}
                                onDelete={handleDelete}
                            />
                        </div>

                        {activeSidebar === "search" && (
                            <ProjectSearchPanel
                                projectId={numericProjectId}
                                onFileSelect={openFile}
                            />
                        )}

                        {activeSidebar === "source-control" && (
                            <section className="ide-tool-view" aria-label="Source control">
                                <header className="ide-tool-view__header">
                                    <h2>Source Control</h2>
                                    <button
                                        type="button"
                                        title="Refresh Git status"
                                        aria-label="Refresh Git status"
                                        onClick={() => {
                                            void getGitStatus(numericProjectId)
                                                .then((status) => {
                                                    setGitStatus(status);
                                                    setGitError("");
                                                })
                                                .catch(() => {
                                                    setGitError("Git status unavailable");
                                                });
                                        }}
                                    >
                                        Refresh
                                    </button>
                                </header>
                                <div className="ide-tool-view__body">
                                    <p className="ide-source-branch">
                                        {gitStatus?.branch || gitError || "Loading Git status…"}
                                    </p>
                                    {gitStatus?.changed_files.length ? (
                                        <>
                                            <p className="ide-tool-view__message">
                                                {gitStatus.changed_files.length} changed file
                                                {gitStatus.changed_files.length === 1 ? "" : "s"}
                                            </p>
                                            {gitStatus.changed_files.map((file) => (
                                                <div className="ide-source-file" key={file.path}>
                                                    <code>{file.path}</code>
                                                    <span>{file.status}</span>
                                                </div>
                                            ))}
                                            <button
                                                type="button"
                                                className="ide-tool-view__action"
                                                onClick={() => {
                                                    setGitDiff("");
                                                    void openGitDiff(false);
                                                }}
                                            >
                                                View working tree diff
                                            </button>
                                        </>
                                    ) : (
                                        <p className="ide-tool-view__message">
                                            {gitStatus
                                                ? "No local changes."
                                                : "Git status is read-only in this IDE."}
                                        </p>
                                    )}
                                </div>
                            </section>
                        )}

                        {activeSidebar === "run-debug" && (
                            <section className="ide-tool-view" aria-label="Run and debug">
                                <header className="ide-tool-view__header">
                                    <h2>Run and Debug</h2>
                                </header>
                                <div className="ide-tool-view__body">
                                    <p className="ide-tool-view__message">
                                        Run project commands in the integrated terminal. A debugger
                                        is not configured for this workspace.
                                    </p>
                                </div>
                            </section>
                        )}

                        {activeSidebar === "extensions" && (
                            <section className="ide-tool-view" aria-label="Extensions">
                                <header className="ide-tool-view__header">
                                    <h2>Extensions</h2>
                                </header>
                                <div className="ide-tool-view__body">
                                    <p className="ide-tool-view__message">
                                        Extension management is not available in this web IDE.
                                    </p>
                                </div>
                            </section>
                        )}

                        {activeSidebar === "accounts" && (
                            <section className="ide-tool-view" aria-label="Accounts">
                                <header className="ide-tool-view__header">
                                    <h2>Account</h2>
                                </header>
                                <div className="ide-tool-view__body">
                                    <p className="ide-tool-view__message">
                                        Manage your signed-in IDE session.
                                    </p>
                                    <button
                                        type="button"
                                        className="ide-tool-view__action"
                                        onClick={() => navigate("/projects")}
                                    >
                                        Back to projects
                                    </button>
                                    <button
                                        type="button"
                                        className="ide-tool-view__action"
                                        onClick={handleLogout}
                                    >
                                        Sign out
                                    </button>
                                </div>
                            </section>
                        )}

                        {activeSidebar === "settings" && (
                            <section className="ide-tool-view" aria-label="Settings">
                                <header className="ide-tool-view__header">
                                    <h2>Settings</h2>
                                </header>
                                <div className="ide-tool-view__body">
                                    <p className="ide-tool-view__message">
                                        Restore the default editor, explorer, and terminal sizes.
                                    </p>
                                    <button
                                        type="button"
                                        className="ide-tool-view__action"
                                        onClick={resetLayout}
                                    >
                                        Restore default layout
                                    </button>
                                </div>
                            </section>
                        )}

                    </aside>


                    {/* ================================= */}
                    {/* RESIZER: EXPLORER / CENTER */}
                    {/* ================================= */}

                    {activeSidebar && (
                        <div
                            className={`ide-resizer ide-resizer-col ${activeResizer === "explorer"
                                ? "is-active"
                                : ""
                                }`}
                            onMouseDown={event => {
                                event.preventDefault();
                                setActiveResizer("explorer");
                            }}
                            onDoubleClick={() => setExplorerWidth(240)}
                            title="Drag to resize explorer (Double-click to reset)"
                        >
                            <div className="ide-resizer-handle" />
                        </div>
                    )}

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
                                                    onContextMenu={(event) => {
                                                        event.preventDefault();
                                                        setActiveTabId(tab.file.id);
                                                        setTabContextMenu({
                                                            fileId: tab.file.id,
                                                            x: Math.max(
                                                                8,
                                                                Math.min(
                                                                    event.clientX,
                                                                    window.innerWidth - 196,
                                                                ),
                                                            ),
                                                            y: Math.max(
                                                                8,
                                                                Math.min(
                                                                    event.clientY,
                                                                    window.innerHeight - 124,
                                                                ),
                                                            ),
                                                        });
                                                    }}
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

                                    {activeTab && (
                                        <nav
                                            className="editor-breadcrumbs"
                                            aria-label="File breadcrumbs"
                                        >
                                            <div className="editor-breadcrumbs__path">
                                                {(activeTab.file.path || activeTab.file.name)
                                                    .split(/[\\/]/)
                                                    .filter(Boolean)
                                                    .map((segment, index) => (
                                                        <span
                                                            className="editor-breadcrumbs__segment"
                                                            key={`${index}-${segment}`}
                                                        >
                                                            {segment}
                                                        </span>
                                                    ))}
                                            </div>
                                            <span className="editor-breadcrumbs__language">
                                                {activeTab.language}
                                            </span>
                                        </nav>
                                    )}


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
                                                ref={editorRef}
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
                                                onSelectionChange={(selection) => {
                                                    setSelectedCode(
                                                        selection
                                                            ? {
                                                                code: selection.text,
                                                                startLine: selection.startLine,
                                                                endLine: selection.endLine,
                                                            }
                                                            : null,
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
                            hidden={!isTerminalVisible}
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
                                display: isTerminalVisible ? "flex" : "none",
                            }}
                        >

                            <Terminal
                                ref={terminalRef}
                                projectId={
                                    numericProjectId
                                }
                                onResultChange={setTerminalContext}
                                onAskAI={() => aiChatRef.current?.askAboutTerminal()}
                            />

                        </section>

                    </div>


                    {/* ================================= */}
                    {/* RESIZER: CENTER / AI PANEL */}
                    {/* ================================= */}

                    {isChatVisible && (
                        <div
                            className={`ide-resizer ide-resizer-col ${activeResizer === "ai"
                                ? "is-active"
                                : ""
                                }`}
                            onMouseDown={event => {
                                event.preventDefault();
                                setActiveResizer("ai");
                            }}
                            onDoubleClick={() => setAiPanelWidth(360)}
                            title="Drag to resize AI panel (Double-click to reset)"
                        >
                            <div className="ide-resizer-handle" />
                        </div>
                    )}


                    {/* ================================= */}
                    {/* RIGHT: AI ASSISTANT */}
                    {/* ================================= */}

                    <aside
                        className="ide-ai-panel"
                        style={{
                            width: `${aiPanelWidth}px`,
                            flexBasis: `${aiPanelWidth}px`,
                            display: isChatVisible ? "flex" : "none",
                        }}
                    >
                        <AIChat
                            ref={aiChatRef}
                            projectId={numericProjectId}
                            hasUnsavedChanges={tabs.some((tab) => tab.isDirty)}
                            onAgentChangesApplied={handleAgentChangesApplied}
                            context={activeTab?.content ?? null}
                            fileName={activeTab?.file.name ?? null}
                            filePath={activeTab?.file.path ?? activeTab?.file.name ?? null}
                            language={activeTab?.language ?? null}
                            selectedCode={
                                activeTab && selectedCode
                                    ? {
                                        filePath: activeTab.file.path ?? activeTab.file.name,
                                        language: activeTab.language,
                                        ...selectedCode,
                                    }
                                    : null
                            }
                                    terminalContext={terminalContext}
                                    onApplyPatch={handleApplyPatch}
                            onApplyCode={handleApplyCode}
                            onUndoCode={handleUndoAIChange}
                            canUndo={
                                aiUndoHistory.some(
                                    (entry) =>
                                        entry.tabId ===
                                        activeTabId,
                                )
                            }
                            undoCount={
                                aiUndoHistory.filter(
                                    (entry) =>
                                        entry.tabId ===
                                        activeTabId,
                                ).length
                            }
                            undoHistory={aiUndoHistory
                                .filter(
                                    (entry) =>
                                        entry.tabId ===
                                        activeTabId,
                                )
                                .map((entry) => ({
                                    id: entry.id,
                                    description:
                                        entry.description,
                                    timestamp:
                                        entry.timestamp,
                                }))}
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

            <footer className="ide-status-bar" aria-label="IDE status">
                <button
                    type="button"
                    className="ide-status-bar__git"
                    onClick={() => {
                        setGitDiff("");
                        void openGitDiff(false);
                    }}
                    title={gitError || "Open read-only Git status and diff"}
                    aria-label={`Git ${gitStatus?.branch || gitError || "status loading"}; ${gitStatus?.changed_files.length ?? 0} changed files`}
                >
                    <GitBranch size={13} aria-hidden="true" />
                    <span>{gitStatus?.branch || (gitError ? "Git unavailable" : "Loading Git status")}</span>
                    {gitStatus && (
                        <span className="ide-status-bar__changes">
                            {gitStatus.changed_files.length}
                        </span>
                    )}
                </button>
            </footer>

        </div>

    );
}