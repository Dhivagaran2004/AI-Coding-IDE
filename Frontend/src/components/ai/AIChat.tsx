import {
    forwardRef,
    useEffect,
    useImperativeHandle,
    useMemo,
    useRef,
    useState,
} from "react";
import axios from "axios";
import { DiffEditor } from "@monaco-editor/react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
    ChevronDown,
    Check,
    MoreVertical,
    Plus,
} from "lucide-react";
import "./AIChat.css";
import {
    approveAgentChanges,
    approveAgentValidation,
    cancelAgentTask,
    clearAIConversation,
    continueAgentTask,
    createAIConversation,
    createAgentTask,
    deleteAIConversation,
    getAIConversation,
    getAgentTasks,
    listAIConversations,
    renameAIConversation,
    streamAIChat,
    getAgentTask,
    undoAgentTask,
    type AIConversationSummary,
    type AgentTask,
    type AIMessage,
    type CodeAction,
} from "../../services/aiService";
import type { TerminalAIContext } from "../../services/terminalService";

type AIHistoryItem = {
    id: string;
    description: string;
    timestamp: number;
};

type AIChatProps = {
    projectId?: number;
    context?: string | null;
    fileName?: string | null;
    filePath?: string | null;
    language?: string | null;
    selectedCode?: {
        filePath: string;
        language: string;
        code: string;
        startLine: number;
        endLine: number;
    } | null;
    terminalContext?: TerminalAIContext | null;
    hasUnsavedChanges?: boolean;
    onApplyPatch?: (action: CodeAction) => Promise<void>;
    onAgentChangesApplied?: (
        changes: { path: string; content: string; file_id: number }[],
    ) => void;
    onApplyCode?: (
        code: string,
        description?: string,
    ) => void;

    onUndoCode?: () => void;

    canUndo?: boolean;

    undoCount?: number;

    undoHistory?: AIHistoryItem[];
};

export type AIChatHandle = {
    askAboutTerminal: () => void;
};

type ChatMessage = {
    id: number;
    role: "user" | "assistant";
    content: string;
    code?: string;
    codeAction?: CodeAction;
};

type PreviewAction =
    | "general"
    | "fix"
    | "optimize"
    | "explain"
    | "tests";

type DiffLineType =
    | "added"
    | "removed"
    | "unchanged";

type DiffLine = {
    id: string;
    type: DiffLineType;
    text: string;
    oldLineNumber?: number;
    newLineNumber?: number;
};

type DiffStats = {
    added: number;
    removed: number;
    unchanged: number;
};

/* =========================================================
   CODE BLOCK EXTRACTION
   ========================================================= */

function extractCodeBlock(
    content: string,
    fileName?: string | null,
): string | null {
    const matches = [
        ...content.matchAll(
            /```([a-zA-Z0-9_+#.-]+)?\s*\n?([\s\S]*?)```/g,
        ),
    ];

    if (matches.length === 0) {
        return null;
    }

    type CodeBlock = {
        language: string;
        code: string;
    };

    const blocks: CodeBlock[] = matches
        .map((match) => ({
            language: (match[1] ?? "").toLowerCase(),
            code: match[2]
                .replace(/\r\n/g, "\n")
                .trim(),
        }))
        .filter(
            (block) =>
                block.code.length > 0,
        );

    if (blocks.length === 0) {
        return null;
    }

    /*
     * Determine the language of the currently
     * opened editor file.
     */
    const extension =
        fileName
            ?.split(".")
            .pop()
            ?.toLowerCase() ?? "";

    const languageAliases: Record<
        string,
        string[]
    > = {
        py: ["python", "py"],
        js: ["javascript", "js"],
        jsx: ["javascript", "jsx", "js"],
        ts: ["typescript", "ts"],
        tsx: ["typescript", "tsx", "ts"],
        java: ["java"],
        cpp: ["cpp", "c++"],
        c: ["c"],
        cs: ["csharp", "cs"],
        go: ["go", "golang"],
        rs: ["rust", "rs"],
        php: ["php"],
        rb: ["ruby", "rb"],
        sql: ["sql"],
        html: ["html"],
        css: ["css"],
        scss: ["scss"],
        json: ["json"],
        xml: ["xml"],
        sh: ["bash", "shell", "sh"],
        ps1: ["powershell", "ps1"],
    };

    const preferredLanguages =
        languageAliases[extension] ?? [];

    /*
     * First preference:
     * choose the code block matching the
     * currently opened file.
     */
    if (preferredLanguages.length > 0) {
        const matchingBlock =
            blocks.find((block) =>
                preferredLanguages.includes(
                    block.language,
                ),
            );

        if (matchingBlock) {
            return matchingBlock.code;
        }
    }

    /*
     * Second preference:
     * Ignore obvious command/config blocks
     * when another larger implementation block
     * exists.
     */
    const nonCommandBlocks =
        blocks.filter(
            (block) =>
                ![
                    "bash",
                    "shell",
                    "sh",
                    "powershell",
                    "ps1",
                    "cmd",
                    "console",
                    "terminal",
                ].includes(block.language),
        );

    if (nonCommandBlocks.length > 0) {
        return nonCommandBlocks.sort(
            (a, b) =>
                b.code.length -
                a.code.length,
        )[0].code;
    }

    /*
     * Final fallback:
     * choose the largest block.
     */
    return blocks.sort(
        (a, b) =>
            b.code.length -
            a.code.length,
    )[0].code;
}

/* =========================================================
   ACTION DETECTION
   ========================================================= */

function detectPreviewAction(
    message: string,
): PreviewAction {
    const normalized =
        message.toLowerCase();

    if (
        normalized.includes(
            "generate comprehensive unit tests",
        ) ||
        normalized.includes(
            "generate tests",
        ) ||
        normalized.includes(
            "unit tests",
        )
    ) {
        return "tests";
    }

    if (
        normalized.includes(
            "find the bugs",
        ) ||
        normalized.includes(
            "fix the bugs",
        ) ||
        normalized.includes(
            "corrected code",
        ) ||
        normalized.includes(
            "fix code",
        )
    ) {
        return "fix";
    }

    if (
        normalized.includes(
            "optimize this code",
        ) ||
        normalized.includes(
            "optimized code",
        ) ||
        normalized.includes(
            "optimize",
        )
    ) {
        return "optimize";
    }

    if (
        normalized.includes(
            "explain this code",
        ) ||
        normalized.includes(
            "explain the code",
        )
    ) {
        return "explain";
    }

    return "general";
}

/* =========================================================
   ACTION LABEL
   ========================================================= */

function getActionLabel(
    action: PreviewAction,
): string {
    switch (action) {
        case "fix":
            return "Fix Code";

        case "optimize":
            return "Optimize Code";

        case "explain":
            return "Explain Code";

        case "tests":
            return "Generate Tests";

        default:
            return "AI Code Change";
    }
}

/* =========================================================
   LINE DIFF ALGORITHM
   ========================================================= */

function createLineDiff(
    original: string,
    modified: string,
): DiffLine[] {
    const oldLines =
        original.replace(/\r\n/g, "\n").split("\n");

    const newLines =
        modified.replace(/\r\n/g, "\n").split("\n");

    /*
     * Empty content should behave like an empty file
     * rather than a file containing one empty line.
     */
    const normalizedOld =
        original.length === 0 ? [] : oldLines;

    const normalizedNew =
        modified.length === 0 ? [] : newLines;

    /*
     * For very large files, a full LCS matrix can become
     * expensive. In that case we fall back to showing
     * the entire old file as removed and the entire new
     * file as added.
     */
    const MAX_DIFF_CELLS = 1_500_000;

    if (
        normalizedOld.length *
            normalizedNew.length >
        MAX_DIFF_CELLS
    ) {
        const result: DiffLine[] = [];

        normalizedOld.forEach(
            (line, index) => {
                result.push({
                    id: `removed-${index}`,
                    type: "removed",
                    text: line,
                    oldLineNumber:
                        index + 1,
                });
            },
        );

        normalizedNew.forEach(
            (line, index) => {
                result.push({
                    id: `added-${index}`,
                    type: "added",
                    text: line,
                    newLineNumber:
                        index + 1,
                });
            },
        );

        return result;
    }

    /*
     * LCS matrix.
     *
     * dp[i][j] represents the longest common
     * subsequence between:
     *
     * oldLines[i...]
     * newLines[j...]
     */
    const dp = Array.from(
        {
            length:
                normalizedOld.length + 1,
        },
        () =>
            new Array(
                normalizedNew.length + 1,
            ).fill(0),
    );

    for (
        let i = normalizedOld.length - 1;
        i >= 0;
        i--
    ) {
        for (
            let j = normalizedNew.length - 1;
            j >= 0;
            j--
        ) {
            if (
                normalizedOld[i] ===
                normalizedNew[j]
            ) {
                dp[i][j] =
                    dp[i + 1][j + 1] + 1;
            } else {
                dp[i][j] = Math.max(
                    dp[i + 1][j],
                    dp[i][j + 1],
                );
            }
        }
    }

    const result: DiffLine[] = [];

    let i = 0;
    let j = 0;

    let oldLineNumber = 1;
    let newLineNumber = 1;

    while (
        i < normalizedOld.length &&
        j < normalizedNew.length
    ) {
        if (
            normalizedOld[i] ===
            normalizedNew[j]
        ) {
            result.push({
                id: `unchanged-${i}-${j}`,
                type: "unchanged",
                text: normalizedOld[i],
                oldLineNumber,
                newLineNumber,
            });

            i++;
            j++;

            oldLineNumber++;
            newLineNumber++;

            continue;
        }

        if (
            dp[i + 1][j] >=
            dp[i][j + 1]
        ) {
            result.push({
                id: `removed-${i}-${j}`,
                type: "removed",
                text: normalizedOld[i],
                oldLineNumber,
            });

            i++;
            oldLineNumber++;
        } else {
            result.push({
                id: `added-${i}-${j}`,
                type: "added",
                text: normalizedNew[j],
                newLineNumber,
            });

            j++;
            newLineNumber++;
        }
    }

    while (
        i < normalizedOld.length
    ) {
        result.push({
            id: `removed-tail-${i}`,
            type: "removed",
            text: normalizedOld[i],
            oldLineNumber,
        });

        i++;
        oldLineNumber++;
    }

    while (
        j < normalizedNew.length
    ) {
        result.push({
            id: `added-tail-${j}`,
            type: "added",
            text: normalizedNew[j],
            newLineNumber,
        });

        j++;
        newLineNumber++;
    }

    return result;
}

/* =========================================================
   DIFF STATISTICS
   ========================================================= */

function calculateDiffStats(
    diff: DiffLine[],
): DiffStats {
    let added = 0;
    let removed = 0;
    let unchanged = 0;

    diff.forEach((line) => {
        if (line.type === "added") {
            added++;
        } else if (
            line.type === "removed"
        ) {
            removed++;
        } else {
            unchanged++;
        }
    });

    return {
        added,
        removed,
        unchanged,
    };
}

/* =========================================================
   BUILD MODIFIED CODE FROM DIFF
   ========================================================= */

function buildSelectedCode(
    diff: DiffLine[],
): string {
    /*
     * The resulting file consists of:
     *
     *   unchanged lines
     *   added lines
     *
     * Removed lines are excluded.
     */
    return diff
        .filter(
            (line) =>
                line.type !== "removed",
        )
        .map((line) => line.text)
        .join("\n");
}

/* =========================================================
   AI CHAT COMPONENT
   ========================================================= */

const AIChat = forwardRef<AIChatHandle, AIChatProps>(function AIChat({
    projectId,
    context,
    fileName,
    filePath,
    language,
    selectedCode,
    terminalContext,
    hasUnsavedChanges = false,
    onApplyPatch,
    onAgentChangesApplied,
    onApplyCode,
    onUndoCode,
    canUndo = false,
    undoCount = 0,
    undoHistory = [],
}: AIChatProps, ref) {
    /* =====================================================
       STATE
       ===================================================== */

    const [input, setInput] =
        useState("");

    const [
        messages,
        setMessages,
    ] = useState<ChatMessage[]>([
        {
            id: 1,
            role: "assistant",
            content:
                "Hello! I'm your AI coding assistant. Ask me about your code, debugging, or development tasks.",
        },
    ]);

    const [
        isLoading,
        setIsLoading,
    ] = useState(false);

    const [
        previewCode,
        setPreviewCode,
    ] = useState<string | null>(
        null,
    );

    const [
        previewAction,
        setPreviewAction,
    ] = useState<PreviewAction>(
        "general",
    );

    const [
        previewMessageId,
        setPreviewMessageId,
    ] = useState<number | null>(
        null,
    );

    const [previewPatch, setPreviewPatch] =
        useState<CodeAction | null>(null);

    const [previewError, setPreviewError] = useState<string | null>(null);

    const [isApplyingPatch, setIsApplyingPatch] =
        useState(false);
    const agentApprovalInFlightRef = useRef(false);
    const [mode, setMode] = useState<"ask" | "plan" | "edit" | "agent">("ask");
    const [isModeMenuOpen, setIsModeMenuOpen] = useState(false);
    const modePickerRef = useRef<HTMLDivElement | null>(null);
    const modePickerButtonRef = useRef<HTMLButtonElement | null>(null);
    const [isActionsMenuOpen, setIsActionsMenuOpen] = useState(false);
    const actionsMenuRef = useRef<HTMLDivElement | null>(null);
    const actionsMenuButtonRef = useRef<HTMLButtonElement | null>(null);
    const [isConversationMenuOpen, setIsConversationMenuOpen] = useState(false);
    const conversationMenuRef = useRef<HTMLDivElement | null>(null);
    const conversationMenuButtonRef = useRef<HTMLButtonElement | null>(null);
    const [agentTask, setAgentTask] = useState<AgentTask | null>(null);
    const [agentUndoError, setAgentUndoError] = useState<string | null>(null);
    const [isUndoingAgentTask, setIsUndoingAgentTask] = useState(false);
    const [previewAgentActionIndex, setPreviewAgentActionIndex] = useState<number | null>(null);
    const generationAbortRef = useRef<AbortController | null>(null);
    const [conversations, setConversations] = useState<AIConversationSummary[]>([]);
    const [conversationId, setConversationId] = useState<number | null>(null);

    const inputRef =
        useRef<HTMLTextAreaElement | null>(
            null,
        );

    const messagesEndRef =
        useRef<HTMLDivElement | null>(
            null,
        );

    /* =====================================================
       DIFF
       ===================================================== */

    const diffLines = useMemo(() => {
        if (previewCode === null) {
            return [];
        }

        return createLineDiff(
            previewPatch?.old_code ?? context ?? "",
            previewCode,
        );
    }, [
        context,
        previewPatch,
        previewCode,
    ]);

    const diffStats = useMemo(() => {
        return calculateDiffStats(
            diffLines,
        );
    }, [diffLines]);

    /* =====================================================
       SCROLL
       ===================================================== */

    const scrollToBottom = () => {
        messagesEndRef.current?.scrollIntoView(
            {
                behavior: "smooth",
            },
        );
    };

    useEffect(() => {
        scrollToBottom();
    }, [
        messages,
        isLoading,
        previewCode,
    ]);

    /* =====================================================
       AUTO FOCUS
       ===================================================== */

    useEffect(() => {
        if (!isLoading) {
            inputRef.current?.focus();
        }
    }, [isLoading]);

    useEffect(() => {
        if (!isModeMenuOpen) {
            return;
        }

        const closeOnOutsidePointer = (event: PointerEvent) => {
            if (!modePickerRef.current?.contains(event.target as Node)) {
                setIsModeMenuOpen(false);
            }
        };
        const closeOnEscape = (event: KeyboardEvent) => {
            if (event.key === "Escape") {
                setIsModeMenuOpen(false);
                modePickerButtonRef.current?.focus();
            }
        };

        document.addEventListener("pointerdown", closeOnOutsidePointer);
        document.addEventListener("keydown", closeOnEscape);
        return () => {
            document.removeEventListener("pointerdown", closeOnOutsidePointer);
            document.removeEventListener("keydown", closeOnEscape);
        };
    }, [isModeMenuOpen]);

    useEffect(() => {
        if (!isActionsMenuOpen) {
            return;
        }

        const closeOnOutsidePointer = (event: PointerEvent) => {
            if (!actionsMenuRef.current?.contains(event.target as Node)) {
                setIsActionsMenuOpen(false);
            }
        };
        const closeOnEscape = (event: KeyboardEvent) => {
            if (event.key === "Escape") {
                setIsActionsMenuOpen(false);
                actionsMenuButtonRef.current?.focus();
            }
        };

        document.addEventListener("pointerdown", closeOnOutsidePointer);
        document.addEventListener("keydown", closeOnEscape);
        return () => {
            document.removeEventListener("pointerdown", closeOnOutsidePointer);
            document.removeEventListener("keydown", closeOnEscape);
        };
    }, [isActionsMenuOpen]);

    useEffect(() => {
        if (!isConversationMenuOpen) {
            return;
        }

        const closeOnOutsidePointer = (event: PointerEvent) => {
            if (!conversationMenuRef.current?.contains(event.target as Node)) {
                setIsConversationMenuOpen(false);
            }
        };
        const closeOnEscape = (event: KeyboardEvent) => {
            if (event.key === "Escape") {
                setIsConversationMenuOpen(false);
                conversationMenuButtonRef.current?.focus();
            }
        };

        document.addEventListener("pointerdown", closeOnOutsidePointer);
        document.addEventListener("keydown", closeOnEscape);
        return () => {
            document.removeEventListener("pointerdown", closeOnOutsidePointer);
            document.removeEventListener("keydown", closeOnEscape);
        };
    }, [isConversationMenuOpen]);

    useEffect(() => {
        if (
            !agentTask ||
            !["pending", "planning", "executing", "validating"].includes(agentTask.status)
        ) {
            return;
        }
        const timer = window.setInterval(() => {
            void getAgentTask(agentTask.id)
                .then(setAgentTask)
                .catch((error) => console.error("Unable to refresh agent task:", error));
        }, 1200);
        return () => window.clearInterval(timer);
    }, [agentTask?.id, agentTask?.status]);

    useEffect(() => {
        let active = true;
        setConversationId(null);
        setMessages([]);
        void listAIConversations(projectId)
            .then((items) => {
                if (active) {
                    setConversations(items);
                }
            })
            .catch((error) => {
                if (active) {
                    console.error("Unable to load AI conversations:", error);
                }
            });
        return () => {
            active = false;
        };
    }, [projectId]);

    useEffect(() => {
        let active = true;
        void getAgentTasks(projectId)
            .then((tasks) => {
                if (!active || agentTask) {
                    return;
                }
                const recovered = tasks.find((task) =>
                    !["completed", "failed", "cancelled"].includes(task.status),
                );
                if (recovered) {
                    setAgentTask(recovered);
                }
            })
            .catch((error) => {
                if (active) {
                    console.error("Unable to recover agent tasks:", error);
                }
            });
        return () => {
            active = false;
        };
    }, [projectId]);

    useEffect(
        () => () => generationAbortRef.current?.abort(),
        [],
    );

    /* =====================================================
       ADD MESSAGE
       ===================================================== */

    const addMessage = (
        role:
            | "user"
            | "assistant",
        content: string,
        code?: string,
    ) => {
        setMessages(
            (previous) => [
                ...previous,
                {
                    id:
                        Date.now() +
                        Math.random(),
                    role,
                    content,
                    code,
                },
            ],
        );
    };

    /* =====================================================
       OPEN PREVIEW
       ===================================================== */

    const handlePreviewCode = (
        code: string,
        messageId: number,
        action: PreviewAction,
    ) => {
        setPreviewPatch(null);
        setPreviewCode(code);
        setPreviewMessageId(
            messageId,
        );
        setPreviewAction(action);
    };

    const handlePreviewPatch = (
        action: CodeAction,
        messageId: number,
    ) => {
        setPreviewError(null);
        setPreviewPatch(action);
        setPreviewCode(action.new_code);
        setPreviewMessageId(messageId);
        setPreviewAction("general");
    };

    const handlePreviewAgentAction = (action: CodeAction, index: number) => {
        setPreviewError(null);
        setPreviewAgentActionIndex(index);
        setPreviewPatch(action);
        setPreviewCode(action.new_code);
        setPreviewMessageId(null);
        setPreviewAction("general");
    };

    /* =====================================================
       CLOSE PREVIEW
       ===================================================== */

    const closePreview = () => {
        setPreviewCode(null);
        setPreviewPatch(null);
        setPreviewError(null);
        setPreviewMessageId(null);
        setPreviewAction("general");
        setPreviewAgentActionIndex(null);
    };

    const handleAgentApproval = async (
        approval: {
            action_indexes?: number[];
            reject_indexes?: number[];
            accept_all?: boolean;
            reject_all?: boolean;
        },
        previewActionIndex?: number,
    ) => {
        if (!agentTask || agentApprovalInFlightRef.current) {
            return;
        }

        agentApprovalInFlightRef.current = true;
        const task = agentTask;
        const approvedIndexes = approval.accept_all
            ? task.actions.flatMap((item, index) =>
                item.status === "awaiting_approval" ? [index] : [],
            )
            : approval.action_indexes ?? [];

        setPreviewError(null);
        setIsApplyingPatch(true);
        try {
            const updated = await approveAgentChanges(task.id, approval);
            setAgentTask(updated);
            if (updated.changes?.length) {
                onAgentChangesApplied?.(updated.changes);
            }

            const unappliedIndex = approvedIndexes.find(
                (index) => updated.actions[index]?.status !== "applied",
            );
            if (unappliedIndex !== undefined) {
                throw new Error(
                    updated.actions[unappliedIndex]?.error ??
                        "The server did not apply this proposed change.",
                );
            }

            if (previewActionIndex !== undefined) {
                closePreview();
            }
        } catch (error) {
            const errorMessage = axios.isAxiosError(error)
                ? typeof error.response?.data?.detail === "string"
                    ? error.response.data.detail
                    : error.message
                : error instanceof Error
                    ? error.message
                    : "The change could not be applied.";

            if (
                axios.isAxiosError(error) &&
                error.response?.status === 409 &&
                approvedIndexes.length > 0
            ) {
                try {
                    let latest = await getAgentTask(task.id);
                    setAgentTask(latest);
                    const syncAppliedFiles = (changes: AgentTask["changes"]) => {
                        const approvedPaths = new Set(
                            approvedIndexes.flatMap((index) => {
                                const path = task.actions[index]?.action.file_path;
                                return path ? [path] : [];
                            }),
                        );
                        const appliedChanges = (changes ?? []).filter(
                            (change) => approvedPaths.has(change.path),
                        );
                        if (appliedChanges.length === 0) {
                            return false;
                        }
                        onAgentChangesApplied?.(appliedChanges);
                        if (previewActionIndex !== undefined) {
                            closePreview();
                        }
                        return true;
                    };

                    const allApplied = () => approvedIndexes.every(
                        (index) => latest.actions[index]?.status === "applied",
                    );
                    if (allApplied() && syncAppliedFiles(latest.changes)) {
                        return;
                    }

                    const canRetry = latest.status === "awaiting_approval"
                        && approvedIndexes.every(
                            (index) => latest.actions[index]?.status === "awaiting_approval",
                        );
                    if (canRetry) {
                        try {
                            latest = await approveAgentChanges(task.id, {
                                action_indexes: approvedIndexes,
                            });
                            setAgentTask(latest);
                            if (
                                approvedIndexes.every(
                                    (index) => latest.actions[index]?.status === "applied",
                                ) &&
                                syncAppliedFiles(latest.changes)
                            ) {
                                return;
                            }
                        } catch (retryError) {
                            const retryMessage = axios.isAxiosError(retryError)
                                ? typeof retryError.response?.data?.detail === "string"
                                    ? retryError.response.data.detail
                                    : retryError.message
                                : retryError instanceof Error
                                    ? retryError.message
                                    : "The approval retry failed.";
                            setPreviewError(
                                `Unable to apply this change: ${retryMessage} (task status: ${latest.status})`,
                            );
                            return;
                        }
                    }

                    const actionStatus = approvedIndexes
                        .map((index) => latest.actions[index]?.status ?? "missing")
                        .join(", ");
                    setPreviewError(
                        `Unable to apply this change: ${errorMessage} (task status: ${latest.status}; change status: ${actionStatus})`,
                    );
                    return;
                } catch (refreshError) {
                    const refreshMessage = axios.isAxiosError(refreshError)
                        ? refreshError.message
                        : refreshError instanceof Error
                            ? refreshError.message
                            : "Unable to refresh the agent task.";
                    setPreviewError(
                        `Unable to apply this change: ${errorMessage} (refresh failed: ${refreshMessage})`,
                    );
                    return;
                }
            }

            if (previewActionIndex !== undefined) {
                setPreviewError(`Unable to apply this change: ${errorMessage}`);
            } else {
                addMessage("assistant", `Unable to apply agent changes: ${errorMessage}`);
            }
        } finally {
            agentApprovalInFlightRef.current = false;
            setIsApplyingPatch(false);
        }
    };

    /* =====================================================
       APPLY PREVIEW
       ===================================================== */

    const handleApplyPreview = async () => {
        if (
            previewCode === null
        ) {
            return;
        }

        if (
            hasUnsavedChanges
            && (previewAgentActionIndex !== null || !previewPatch)
        ) {
            addMessage(
                "assistant",
                "Save or discard your open editor changes before applying this AI change.",
            );
            return;
        }

        if (previewPatch) {
            if (previewAgentActionIndex !== null && agentTask) {
                await handleAgentApproval(
                    { action_indexes: [previewAgentActionIndex] },
                    previewAgentActionIndex,
                );
                return;
            }
            if (!onApplyPatch || isApplyingPatch) {
                return;
            }
            setIsApplyingPatch(true);
            try {
                await onApplyPatch(previewPatch);
                closePreview();
            } catch (error) {
                const errorMessage = axios.isAxiosError(error)
                    ? typeof error.response?.data?.detail === "string"
                        ? error.response.data.detail
                        : error.message
                    : error instanceof Error
                        ? error.message
                        : "The patch could not be applied.";
                setPreviewError(`Unable to apply this change: ${errorMessage}`);
            } finally {
                setIsApplyingPatch(false);
            }
            return;
        }

        const selectedCode =
            buildSelectedCode(
                diffLines,
            );

        const description =
            getActionLabel(
                previewAction,
            );

        onApplyCode?.(
            selectedCode,
            description,
        );

        closePreview();
    };

    /* =====================================================
       REJECT PREVIEW
       ===================================================== */

    const handleRejectPreview = () => {
        if (previewAgentActionIndex !== null && agentTask) {
            void approveAgentChanges(agentTask.id, {
                reject_indexes: [previewAgentActionIndex],
            }).then(setAgentTask).catch((error) => {
                addMessage("assistant", `Unable to reject change: ${error instanceof Error ? error.message : "Request failed."}`);
            });
        }
        closePreview();
    };

    /* =====================================================
       SEND MESSAGE
       ===================================================== */

    const handleSend = async (
        messageOverride?: string,
        modeOverride?: "ask" | "plan" | "edit" | "agent",
    ) => {
        const message =
            (messageOverride ?? input).trim();
        const sendMode = modeOverride ?? mode;

        if (
            !message ||
            isLoading
        ) {
            return;
        }

        /*
         * Determine what kind of AI action
         * the user requested.
         */
        const action =
            detectPreviewAction(
                message,
            );

        /*
         * Add the user message immediately.
         */
        addMessage(
            "user",
            message,
        );

        setInput("");
        setIsLoading(true);
        let streamingMessageId: number | null = null;
        let streamedContent = "";

        try {
            if (sendMode === "agent") {
                if (!projectId) {
                    throw new Error("Open a project before starting an agent task.");
                }
                if (agentTask && ["pending", "planning", "awaiting_approval", "executing", "validating"].includes(agentTask.status)) {
                    throw new Error("Finish or stop the active agent task before starting another.");
                }
                const task = await createAgentTask(projectId, message, {
                    current_file_path: filePath,
                    context: context && filePath
                        ? `CURRENT FILE: ${filePath}\nLANGUAGE: ${language ?? ""}\n\n${context}`.slice(0, 20000)
                        : context?.slice(0, 20000),
                    selected_code: selectedCode
                        ? {
                            file_path: selectedCode.filePath,
                            language: selectedCode.language,
                            code: selectedCode.code.slice(0, 20000),
                            start_line: selectedCode.startLine,
                            end_line: selectedCode.endLine,
                        }
                        : null,
                    terminal_context: terminalContext
                        ? {
                            ...terminalContext,
                            command: terminalContext.command.slice(0, 2000),
                            stdout: terminalContext.stdout.slice(0, 12000),
                            stderr: terminalContext.stderr.slice(0, 12000),
                        }
                        : null,
                });
                setAgentTask(task);
                addMessage("assistant", "Agent task started. It will prepare a plan and proposed changes for review.");
                return;
            }
            let activeConversationId = conversationId;
            if (activeConversationId === null) {
                const conversation = await createAIConversation(
                    projectId,
                    message.slice(0, 160),
                );
                activeConversationId = conversation.id;
                setConversationId(conversation.id);
                setConversations((previous) => [conversation, ...previous]);
            }
            /*
             * Convert existing chat messages
             * into the format expected by
             * the FastAPI backend.
             */
            const history: AIMessage[] =
                messages.map(
                    (item) => ({
                        role:
                            item.role,
                        content:
                            item.content,
                    }),
                ).filter((item) =>
                    !(item.role === "assistant" &&
                        item.content.startsWith("Hello! I'm your AI coding assistant.")),
                );

            /*
             * Send the current editor content
             * as context.
             */
            const requestController = new AbortController();
            generationAbortRef.current = requestController;
            const responseMessageId = Date.now() + Math.random();
            streamingMessageId = responseMessageId;
            setMessages((previous) => [
                ...previous,
                {
                    id: responseMessageId,
                    role: "assistant",
                    content: "Thinking...",
                },
            ]);

            const response =
                await streamAIChat({
                    message,
                    mode: sendMode,
                    history,
                    context: context && filePath
                        ? `CURRENT FILE: ${filePath}\nLANGUAGE: ${language ?? ""}\n\n${context}`
                        : context,
                    selected_code: selectedCode
                        ? {
                            file_path: selectedCode.filePath,
                            language: selectedCode.language,
                            code: selectedCode.code.slice(0, 20000),
                            start_line: selectedCode.startLine,
                            end_line: selectedCode.endLine,
                        }
                        : null,
                    terminal_context: terminalContext
                        ? {
                            ...terminalContext,
                            command: terminalContext.command.slice(0, 2000),
                            stdout: terminalContext.stdout.slice(0, 12000),
                            stderr: terminalContext.stderr.slice(0, 12000),
                        }
                        : null,
                    project_id: projectId,
                    conversation_id: activeConversationId,
                }, (token) => {
                    streamedContent += token;
                    setMessages((previous) => previous.map((item) =>
                        item.id === streamingMessageId
                            ? { ...item, content: streamedContent }
                            : item,
                    ));
                }, requestController.signal);
            if (response.conversation_id) {
                setConversationId(response.conversation_id);
            }
            void listAIConversations(projectId)
                .then(setConversations)
                .catch((error) => console.error("Unable to refresh AI conversations:", error));

            /*
             * Extract the first Markdown
             * code block from the response.
             */
            const generatedCode = sendMode === "plan"
                ? null
                : response.code_action?.operation === "replace"
                    ? response.code_action.new_code
                    : extractCodeBlock(response.message);

            const responseContent = response.plan?.length
                ? `Plan:\n${response.plan.map((step, index) => `${index + 1}. ${step}`).join("\n")}`
                : response.message;

            /*
             * Add AI response to chat.
             */
            setMessages((previous) => previous.map((item) =>
                item.id === streamingMessageId
                    ? {
                        ...item,
                        content: responseContent,
                        code: generatedCode ?? undefined,
                        codeAction: response.code_action ?? undefined,
                    }
                    : item,
            ));

            /*
             * Do not automatically modify
             * the editor.
             *
             * The user must explicitly
             * preview and apply the code.
             */
            if (
                generatedCode
            ) {
                setPreviewAction(
                    action,
                );
            }
        } catch (error) {
            console.error(
                "AI chat request failed:",
                error,
            );

            const errorMessage =
                error instanceof Error
                    ? error.message
                    : "Unable to connect to the AI service.";

            if (streamingMessageId !== null) {
                const wasCancelled = generationAbortRef.current?.signal.aborted;
                setMessages((previous) => previous.map((item) =>
                    item.id === streamingMessageId
                        ? {
                            ...item,
                            content: wasCancelled
                                ? `${streamedContent}${streamedContent ? "\n\n" : ""}Generation stopped.`
                                : `${streamedContent}${streamedContent ? "\n\n" : ""}⚠️ ${errorMessage}`,
                        }
                        : item,
                ));
            } else {
                addMessage("assistant", `⚠️ ${errorMessage}`);
            }
        } finally {
            generationAbortRef.current = null;
            setIsLoading(false);
        }
    };

    useImperativeHandle(ref, () => ({
        askAboutTerminal: () => {
            if (!terminalContext || terminalContext.success || isLoading) {
                return;
            }
            setMode("ask");
            void handleSend(
                "Explain this terminal failure and suggest a fix. Do not modify files unless I explicitly request a code change.",
                "ask",
            );
        },
    }), [handleSend, isLoading, terminalContext]);

    /* =====================================================
       KEYBOARD HANDLER
       ===================================================== */

    const handleKeyDown = (
        event: React.KeyboardEvent<HTMLTextAreaElement>,
    ) => {
        if (
            event.key === "Enter" &&
            !event.shiftKey
        ) {
            event.preventDefault();

            void handleSend();
        }
    };

    /* =====================================================
       QUICK ACTION
       ===================================================== */

    const setQuickAction = (
        action: PreviewAction,
        prompt: string,
    ) => {
        if (
            !context?.trim() ||
            isLoading
        ) {
            return;
        }

        setPreviewAction(action);

        setInput(prompt);

        inputRef.current?.focus();
    };

    const openConversation = async (id: number) => {
        const conversation = await getAIConversation(id);
        setConversationId(id);
        setMessages(conversation.messages.map((item) => ({
            id: item.id,
            role: item.role,
            content: item.content,
        })));
        closePreview();
    };

    const startNewConversation = () => {
        setConversationId(null);
        setMessages([]);
        closePreview();
    };

    const handleRenameConversation = async () => {
        if (conversationId === null) {
            return;
        }
        const current = conversations.find((item) => item.id === conversationId);
        const title = window.prompt("Conversation name", current?.title ?? "");
        if (!title?.trim()) {
            return;
        }
        const updated = await renameAIConversation(conversationId, title.trim());
        setConversations((previous) =>
            previous.map((item) => item.id === updated.id ? updated : item),
        );
    };

    const handleClearConversation = async () => {
        if (conversationId === null || !window.confirm("Clear all messages in this conversation?")) {
            return;
        }
        await clearAIConversation(conversationId);
        setMessages([]);
        closePreview();
    };

    const handleDeleteConversation = async () => {
        if (conversationId === null || !window.confirm("Delete this conversation permanently?")) {
            return;
        }
        await deleteAIConversation(conversationId);
        setConversations((previous) =>
            previous.filter((item) => item.id !== conversationId),
        );
        setConversationId(null);
        setMessages([]);
        closePreview();
    };

    /* =====================================================
       RENDER
       ===================================================== */

    return (
        <section className="ai-chat">
            {/* =================================================
                HEADER
                ================================================= */}

            <header className="ai-chat__header">
                <div className="ai-chat__header-top">
                    <div className="ai-chat__title-section">
                        <div className="ai-chat__icon">
                            AI
                        </div>

                        <div className="ai-chat__title-stack">
                            <h2 className="ai-chat__title">
                                Chat Panel
                            </h2>
                        </div>
                    </div>

                    <div className="ai-chat__options" ref={actionsMenuRef}>
                        <div className="ai-chat__conversation-actions" ref={conversationMenuRef}>
                            <button
                                type="button"
                                className="ai-chat__new-chat-button"
                                aria-label="New conversation"
                                title="New conversation"
                                onClick={() => {
                                    setIsConversationMenuOpen(false);
                                    setIsActionsMenuOpen(false);
                                    startNewConversation();
                                }}
                                disabled={isLoading}
                            >
                                <Plus size={17} aria-hidden="true" />
                            </button>
                            <button
                                ref={conversationMenuButtonRef}
                                type="button"
                                className="ai-chat__conversation-menu-button"
                                aria-label="Conversation actions"
                                aria-expanded={isConversationMenuOpen}
                                aria-controls="ai-chat-conversation-actions"
                                title="Conversation actions"
                                onClick={() => {
                                    setIsActionsMenuOpen(false);
                                    setIsConversationMenuOpen((open) => !open);
                                }}
                            >
                                <ChevronDown size={14} aria-hidden="true" />
                            </button>
                            {isConversationMenuOpen && (
                                <div
                                    id="ai-chat-conversation-actions"
                                    className="ai-chat__conversation-menu"
                                    role="group"
                                    aria-label="Conversation actions"
                                >
                                    <button
                                        type="button"
                                        className="ai-chat__options-item"
                                        onClick={() => {
                                            setIsConversationMenuOpen(false);
                                            void handleRenameConversation();
                                        }}
                                        disabled={conversationId === null || isLoading}
                                    >
                                        Rename
                                    </button>
                                    <button
                                        type="button"
                                        className="ai-chat__options-item"
                                        onClick={() => {
                                            setIsConversationMenuOpen(false);
                                            void handleClearConversation();
                                        }}
                                        disabled={conversationId === null || isLoading}
                                    >
                                        Clear
                                    </button>
                                    <button
                                        type="button"
                                        className="ai-chat__options-item ai-chat__options-item--danger"
                                        onClick={() => {
                                            setIsConversationMenuOpen(false);
                                            void handleDeleteConversation();
                                        }}
                                        disabled={conversationId === null || isLoading}
                                    >
                                        Delete
                                    </button>
                                </div>
                            )}
                        </div>
                        <button
                            ref={actionsMenuButtonRef}
                            type="button"
                            className="ai-chat__menu-button"
                            aria-label="AI chat options"
                            aria-expanded={isActionsMenuOpen}
                            aria-controls="ai-chat-options"
                            title="Chat options"
                            onClick={() => {
                                setIsConversationMenuOpen(false);
                                setIsActionsMenuOpen((open) => !open);
                            }}
                        >
                            <MoreVertical size={18} aria-hidden="true" />
                        </button>
                        {isActionsMenuOpen && (
                            <div
                                id="ai-chat-options"
                                className="ai-chat__options-menu"
                                role="group"
                                aria-label="Chat options"
                            >
                                <label className="ai-chat__options-label" htmlFor="ai-chat-conversation">
                                    Conversation
                                </label>
                                <select
                                    id="ai-chat-conversation"
                                    className="ai-chat__options-select"
                                    aria-label="AI conversation"
                                    value={conversationId ?? ""}
                                    disabled={isLoading}
                                    onChange={(event) => {
                                        setIsActionsMenuOpen(false);
                                        if (!event.target.value) {
                                            startNewConversation();
                                            return;
                                        }
                                        const id = Number(event.target.value);
                                        if (id) {
                                            void openConversation(id).catch((error) =>
                                                addMessage("assistant", `Unable to open conversation: ${error instanceof Error ? error.message : "Request failed."}`),
                                            );
                                        }
                                    }}
                                >
                                    <option value="">New conversation</option>
                                    {conversations.map((item) => (
                                        <option key={item.id} value={item.id}>{item.title}</option>
                                    ))}
                                </select>
                                <div className="ai-chat__options-divider" />
                                <div className="ai-chat__options-label">Code actions</div>
                                <button
                                    type="button"
                                    className="ai-chat__options-item"
                                    onClick={() => {
                                        setIsActionsMenuOpen(false);
                                        setQuickAction("explain", "Explain this code step by step.");
                                    }}
                                    disabled={!context?.trim() || isLoading}
                                >
                                    Explain code
                                </button>
                                <button
                                    type="button"
                                    className="ai-chat__options-item"
                                    onClick={() => {
                                        setIsActionsMenuOpen(false);
                                        setQuickAction("fix", "Find the bugs in this code, explain them briefly, and provide the corrected code.");
                                    }}
                                    disabled={!context?.trim() || isLoading}
                                >
                                    Fix code
                                </button>
                                <button
                                    type="button"
                                    className="ai-chat__options-item"
                                    onClick={() => {
                                        setIsActionsMenuOpen(false);
                                        setQuickAction("optimize", "Optimize this code for performance, readability, and maintainability. Explain the improvements and provide the optimized code.");
                                    }}
                                    disabled={!context?.trim() || isLoading}
                                >
                                    Optimize code
                                </button>
                                <button
                                    type="button"
                                    className="ai-chat__options-item"
                                    onClick={() => {
                                        setIsActionsMenuOpen(false);
                                        setQuickAction("tests", "Generate comprehensive unit tests for this code. Cover normal cases, edge cases, and error cases. Use the appropriate testing framework for the detected language.");
                                    }}
                                    disabled={!context?.trim() || isLoading}
                                >
                                    Generate tests
                                </button>
                            </div>
                        )}
                    </div>
                </div>
            </header>

            {/* =================================================
                MESSAGES
                ================================================= */}

            <div className="ai-chat__messages">
                {messages.map(
                    (message) => {
                        const messageAction =
                            detectPreviewAction(
                                message.content,
                            );

                        return (
                            <div
                                key={
                                    message.id
                                }
                                className={`ai-chat__message-row ai-chat__message-row--${message.role}`}
                            >
                                <div
                                    className={`ai-chat__avatar ai-chat__avatar--${message.role}`}
                                >
                                    {message.role ===
                                    "assistant"
                                        ? "AI"
                                        : "You"}
                                </div>

                                <div
                                    className={`ai-chat__bubble ai-chat__bubble--${message.role}`}
                                >
                                    <div className="ai-chat__message-content">
                                        {message.role === "assistant" ? (
                                            <ReactMarkdown
                                                remarkPlugins={[remarkGfm]}
                                            >
                                                {message.content}
                                            </ReactMarkdown>
                                        ) : (
                                            message.content
                                        )}
                                    </div>

                                    {message.role ===
                                        "assistant" &&
                                        (message.code || message.codeAction) && (
                                            <div className="ai-chat__code-actions">
                                                <button
                                                    type="button"
                                                    className="ai-chat__preview-button"
                                                    onClick={() => {
                                                        if (message.codeAction) {
                                                            handlePreviewPatch(
                                                                message.codeAction,
                                                                message.id,
                                                            );
                                                        } else if (message.code) {
                                                            handlePreviewCode(
                                                                message.code,
                                                                message.id,
                                                                messageAction,
                                                            );
                                                        }
                                                    }}
                                                >
                                                    {previewMessageId ===
                                                    message.id
                                                        ? "Previewing..."
                                                        : "Preview Code"}
                                                </button>
                                            </div>
                                        )}
                                </div>
                            </div>
                        );
                    },
                )}

                {/* Loading */}

                {isLoading && (
                    <div className="ai-chat__message-row ai-chat__message-row--assistant">
                        <div className="ai-chat__avatar ai-chat__avatar--assistant">
                            AI
                        </div>

                        <div className="ai-chat__bubble ai-chat__bubble--assistant">
                            <div className="ai-chat__typing">
                                <span />
                                <span />
                                <span />
                            </div>
                        </div>
                    </div>
                )}

            {agentTask && (
                <section className="ai-chat__agent-panel ai-chat__bubble ai-chat__bubble--assistant" aria-live="polite">
                    <div className="ai-chat__agent-heading">
                        <div>
                            <strong>Agent activity</strong>
                            <span className={`ai-chat__agent-status is-${agentTask.status}`}>
                                {agentTask.status.replaceAll("_", " ")}
                            </span>
                        </div>
                        {!( ["completed", "failed", "cancelled"].includes(agentTask.status)) && (
                            <button
                                type="button"
                                className="ai-chat__agent-stop"
                                onClick={() => void cancelAgentTask(agentTask.id).then(setAgentTask)}
                            >
                                Stop
                            </button>
                        )}
                    </div>
                    <p className="ai-chat__agent-task">{agentTask.task}</p>
                    {agentTask.steps.length > 0 && (
                        <ol className="ai-chat__agent-steps">
                            {agentTask.steps.slice(-8).map((step) => (
                                <li key={step.sequence} className={`is-${step.status}`}>
                                    <span>{step.description}</span>
                                </li>
                            ))}
                        </ol>
                    )}
                    {agentTask.plan.length > 0 && (
                        <ol className="ai-chat__agent-plan">
                            {agentTask.plan.map((step, index) => <li key={`${index}-${step}`}>{step}</li>)}
                        </ol>
                    )}
                    {agentTask.status === "completed" && agentTask.actions.length === 0 && !agentTask.validation_command && (
                        <p className="ai-chat__agent-empty">
                            Planning finished without proposing file changes. Add a target file or more implementation detail to your task.
                        </p>
                    )}
                    {agentTask.actions.length > 0 && (
                        <div className="ai-chat__agent-changes">
                            <div className="ai-chat__agent-section-title">
                                Proposed files <span>{agentTask.actions.length}</span>
                            </div>
                            {agentTask.actions.map((item, index) => (
                                <div className="ai-chat__agent-change" key={`${item.action.file_path}-${index}`}>
                                    <div className="ai-chat__agent-file">
                                        <strong title={item.action.file_path}>{item.action.file_path}</strong>
                                        <span className={`ai-chat__agent-file-status is-${item.status}`}>
                                            {item.status.replaceAll("_", " ")}
                                        </span>
                                    </div>
                                    <div className="ai-chat__agent-actions">
                                        <button
                                            type="button"
                                            onClick={() => handlePreviewAgentAction(item.action, index)}
                                        >
                                            Review
                                        </button>
                                        {agentTask.status === "awaiting_approval" && item.status === "awaiting_approval" && (
                                            <>
                                                <button
                                                    type="button"
                                                    disabled={hasUnsavedChanges || isApplyingPatch}
                                                    title={hasUnsavedChanges ? "Save or discard editor changes first" : "Approve this file"}
                                                    onClick={() => void handleAgentApproval({ action_indexes: [index] })}
                                                >
                                                    <Check size={12} />
                                                    Accept
                                                </button>
                                                <button
                                                    type="button"
                                                    className="is-secondary"
                                                    disabled={isApplyingPatch}
                                                    onClick={() => void handleAgentApproval({ reject_indexes: [index] })}
                                                >
                                                    Reject
                                                </button>
                                            </>
                                        )}
                                    </div>
                                    {item.error && <p className="ai-chat__agent-error">{item.error}</p>}
                                </div>
                            ))}
                            {agentTask.status === "awaiting_approval" && agentTask.actions.some((item) => item.status === "awaiting_approval") && (
                                <div className="ai-chat__agent-bulk-actions">
                                    <button
                                        className="is-primary"
                                        type="button"
                                        disabled={hasUnsavedChanges || isApplyingPatch}
                                        onClick={() => void handleAgentApproval({ accept_all: true })}
                                    >Accept all changes</button>
                                    <button
                                        type="button"
                                        disabled={isApplyingPatch}
                                        onClick={() => void handleAgentApproval({ reject_all: true })}
                                    >Reject all</button>
                                </div>
                            )}
                        </div>
                    )}
                    {hasUnsavedChanges && agentTask.actions.some((item) => item.status === "awaiting_approval") && (
                        <p className="ai-chat__agent-note">Save or discard open editor changes before applying.</p>
                    )}
                    {agentTask.change_history.some((change) => !change.rolled_back) && (
                        <>
                            <button
                                type="button"
                                className="ai-chat__agent-continue"
                                disabled={isUndoingAgentTask || hasUnsavedChanges || ["pending", "planning", "executing", "validating"].includes(agentTask.status)}
                                title={hasUnsavedChanges ? "Save or discard editor changes first" : "Restore files only if they have not changed since the AI action"}
                                onClick={() => {
                                    if (!window.confirm("Undo all applied changes from this agent task?")) {
                                        return;
                                    }
                                    setAgentUndoError(null);
                                    setIsUndoingAgentTask(true);
                                    void undoAgentTask(agentTask.id)
                                        .then((updated) => {
                                            setAgentTask(updated);
                                            if (updated.changes?.length) {
                                                onAgentChangesApplied?.(updated.changes);
                                            }
                                        })
                                        .catch((error: unknown) => {
                                            const message = axios.isAxiosError(error)
                                                ? typeof error.response?.data?.detail === "string"
                                                    ? error.response.data.detail
                                                    : error.message
                                                : error instanceof Error
                                                    ? error.message
                                                    : "The AI changes could not be undone.";
                                            setAgentUndoError(message);
                                        })
                                        .finally(() => setIsUndoingAgentTask(false));
                                }}
                            >
                                {isUndoingAgentTask ? "Undoing..." : "Undo AI changes"}
                            </button>
                            {agentUndoError && (
                                <p className="ai-chat__agent-error" role="alert">{agentUndoError}</p>
                            )}
                        </>
                    )}
                    {agentTask.validation_command && agentTask.actions.every((item) => item.status !== "awaiting_approval") && (
                        <div className="ai-chat__agent-validation">
                            <span className="ai-chat__agent-validation-label">Validation</span>
                            <code>{agentTask.validation_command}</code>
                            {agentTask.status === "awaiting_approval" && (
                                <div className="ai-chat__agent-actions">
                                    <button type="button" onClick={() => void approveAgentValidation(agentTask.id, agentTask.validation_command!, true).then(setAgentTask)}>
                                        Run validation
                                    </button>
                                    <button type="button" onClick={() => void approveAgentValidation(agentTask.id, agentTask.validation_command!, false).then(setAgentTask)}>
                                        Skip
                                    </button>
                                </div>
                            )}
                        </div>
                    )}
                    {agentTask.validation_result && (
                        <div className={`ai-chat__agent-result${agentTask.validation_result.success ? " is-pass" : " is-fail"}`}>
                            <strong>{agentTask.validation_result.success ? "Validation passed" : `Validation failed (exit ${agentTask.validation_result.exit_code})`}</strong>
                            {(agentTask.validation_result.stderr || agentTask.validation_result.stdout) && (
                                <pre>{(agentTask.validation_result.stderr || agentTask.validation_result.stdout).slice(0, 3000)}</pre>
                            )}
                        </div>
                    )}
                    {(agentTask.status === "failed" && agentTask.iteration < 5 || agentTask.status === "paused") && (
                        <button type="button" className="ai-chat__agent-continue" onClick={() => void continueAgentTask(agentTask.id).then(setAgentTask)}>
                            {agentTask.status === "paused"
                                ? "Reinspect and continue"
                                : agentTask.validation_result
                                    ? "Propose a correction"
                                    : "Retry planning"}
                        </button>
                    )}
                    {agentTask.stop_reason && <p className="ai-chat__agent-note">{agentTask.stop_reason}</p>}
                </section>
            )}

            <div
                ref={
                    messagesEndRef
                }
            />
            </div>

            {/* =================================================
                CODE DIFF PREVIEW
                ================================================= */}

            {previewCode !== null && (
                <div className="ai-chat__preview">
                    {/* Preview header */}

                    <div className="ai-chat__preview-header">
                        <div>
                            <span className="ai-chat__preview-title">
                                {previewPatch
                                    ? `AI Proposed Changes: ${previewPatch.file_path}`
                                    : "AI Proposed Changes"}
                            </span>

                            <span className="ai-chat__preview-subtitle">
                                {previewPatch?.description ??
                                    "Review changes before applying to editor"}
                            </span>
                        </div>

                        <button
                            type="button"
                            className="ai-chat__preview-close"
                            onClick={
                                closePreview
                            }
                            aria-label="Close code preview"
                        >
                            ×
                        </button>
                    </div>

                    {/* Diff summary */}

                    <div className="ai-chat__diff-summary">
                        <span className="ai-chat__diff-added">
                            +{diffStats.added}{" "}
                            added
                        </span>

                        <span className="ai-chat__diff-removed">
                            -{diffStats.removed}{" "}
                            removed
                        </span>

                        <span className="ai-chat__diff-unchanged">
                            {diffStats.unchanged}{" "}
                            unchanged
                        </span>
                    </div>

                    {/* Diff */}

                    <div className="ai-chat__monaco-diff" aria-label="AI change before and after">
                        <DiffEditor
                            height="100%"
                            language={language ?? "plaintext"}
                            theme="vs-dark"
                            original={previewPatch?.old_code ?? context ?? ""}
                            modified={previewCode}
                            options={{
                                automaticLayout: true,
                                readOnly: true,
                                originalEditable: false,
                                renderSideBySide: true,
                                minimap: { enabled: false },
                                scrollBeyondLastLine: false,
                                wordWrap: "on",
                            }}
                        />
                    </div>

                    {previewError && (
                        <p className="ai-chat__preview-error" role="alert">
                            {previewError}
                        </p>
                    )}

                    {/* Preview actions */}

                    <div className="ai-chat__preview-actions">
                        <button
                            type="button"
                            className="ai-chat__reject-button"
                            onClick={
                                handleRejectPreview
                            }
                        >
                            Reject
                        </button>

                        <button
                            type="button"
                            className="ai-chat__apply-button"
                            onClick={
                                handleApplyPreview
                            }
                            disabled={isApplyingPatch}
                        >
                            {isApplyingPatch ? "Applying..." : "Apply"}
                        </button>
                    </div>
                </div>
            )}

            {/* =================================================
                AI CHANGE HISTORY
                ================================================= */}

            {canUndo &&
                undoHistory.length >
                    0 && (
                    <div className="ai-chat__history">
                        <div className="ai-chat__history-header">
                            <div>
                                <div className="ai-chat__history-title">
                                    AI Change History
                                </div>

                                <div className="ai-chat__history-subtitle">
                                    {undoCount} AI
                                    change
                                    {undoCount !==
                                    1
                                        ? "s"
                                        : ""}{" "}
                                    available
                                </div>
                            </div>

                            <button
                                type="button"
                                className="ai-chat__undo-latest"
                                onClick={() =>
                                    onUndoCode?.()
                                }
                            >
                                Undo Latest
                            </button>
                        </div>

                        <div className="ai-chat__history-list">
                            {[
                                ...undoHistory,
                            ]
                                .reverse()
                                .map(
                                    (
                                        item,
                                    ) => (
                                        <div
                                            key={
                                                item.id
                                            }
                                            className="ai-chat__history-item"
                                        >
                                            <div className="ai-chat__history-dot">
                                                ●
                                            </div>

                                            <div className="ai-chat__history-info">
                                                <span className="ai-chat__history-action">
                                                    {
                                                        item.description
                                                    }
                                                </span>

                                                <span className="ai-chat__history-time">
                                                    {new Date(
                                                        item.timestamp,
                                                    ).toLocaleTimeString(
                                                        [],
                                                        {
                                                            hour: "2-digit",
                                                            minute: "2-digit",
                                                        },
                                                    )}
                                                </span>
                                            </div>
                                        </div>
                                    ),
                                )}
                        </div>
                    </div>
                )}

            {/* =================================================
                INPUT
                ================================================= */}

            <div className="ai-chat__input-area">
                <div className="ai-chat__meta-row ai-chat__meta-row--composer">
                    {fileName && (
                        <span className="ai-chat__context">
                            File: {fileName}
                        </span>
                    )}
                    {selectedCode?.code.trim() && (
                        <span
                            className="ai-chat__context"
                            title={`${selectedCode.filePath}:${selectedCode.startLine}-${selectedCode.endLine}`}
                        >
                            Selection: lines {selectedCode.startLine}-{selectedCode.endLine}
                        </span>
                    )}
                    {terminalContext && (
                        <span className="ai-chat__context">
                            Terminal: exit {terminalContext.exit_code}
                        </span>
                    )}
                </div>

                <div className="ai-chat__input-wrapper">
                    <textarea
                        ref={
                            inputRef
                        }
                        className="ai-chat__input"
                        value={input}
                        onChange={(
                            event,
                        ) =>
                            setInput(
                                event.target
                                    .value,
                            )
                        }
                        onKeyDown={
                            handleKeyDown
                        }
                        placeholder={
                            isLoading
                                ? "AI is thinking..."
                                : mode === "agent"
                                    ? "Describe a coding task for the agent..."
                                    : mode === "plan"
                                        ? "Describe a task to plan..."
                                        : mode === "edit"
                                            ? "Describe the change to propose..."
                                            : "Ask AI about your code..."
                        }
                        rows={1}
                        disabled={
                            isLoading
                        }
                    />

                    <button
                        type="button"
                        className="ai-chat__send-button"
                        onClick={() => isLoading
                            ? generationAbortRef.current?.abort()
                            : void handleSend()}
                        disabled={
                            !isLoading && !input.trim()
                        }
                        aria-label={isLoading ? "Stop generation" : "Send message"}
                    >
                        {isLoading
                            ? "Stop"
                            : "↑"}
                    </button>
                </div>

                <div className="ai-chat__composer-footer">
                    <div className="ai-chat__mode-picker" ref={modePickerRef}>
                        <button
                            ref={modePickerButtonRef}
                            type="button"
                            className="ai-chat__mode-trigger"
                            onClick={() => setIsModeMenuOpen((open) => !open)}
                            aria-label={`Chat mode: ${mode}`}
                            aria-haspopup="true"
                            aria-expanded={isModeMenuOpen}
                        >
                            <span>{mode[0].toUpperCase() + mode.slice(1)}</span>
                            <span className="ai-chat__mode-chevron" aria-hidden="true" />
                        </button>
                        {isModeMenuOpen && (
                            <div className="ai-chat__mode-menu" role="group" aria-label="Assistant mode">
                                {(["agent", "ask", "plan", "edit"] as const).map((item) => (
                                    <button
                                        key={item}
                                        type="button"
                                        className="ai-chat__mode-option"
                                        onClick={() => {
                                            setMode(item);
                                            setIsModeMenuOpen(false);
                                            modePickerButtonRef.current?.focus();
                                        }}
                                        aria-pressed={mode === item}
                                    >
                                        <span>{item[0].toUpperCase() + item.slice(1)}</span>
                                        {mode === item && <span aria-hidden="true">✓</span>}
                                    </button>
                                ))}
                            </div>
                        )}
                    </div>

                    <div className="ai-chat__hint">
                        {mode === "agent"
                            ? hasUnsavedChanges
                                ? "Save or discard open editor changes before agent approval"
                                : "Agent changes and validation require your approval"
                            : <><span>Enter</span> to send · <span>Shift + Enter</span> for new line</>}
                    </div>
                </div>
            </div>
        </section>
    );
});

export default AIChat;