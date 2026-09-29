import {
    useEffect,
    useMemo,
    useRef,
    useState,
} from "react";

import "./AIChat.css";

import {
    sendAIChat,
    type AIMessage,
} from "../../services/aiService";

/* =========================================================
   TYPES
   ========================================================= */

type AIHistoryItem = {
    id: string;
    description: string;
    timestamp: number;
};

type AIChatProps = {
    context?: string | null;
    fileName?: string | null;

    onApplyCode?: (
        code: string,
        description?: string,
    ) => void;

    onUndoCode?: () => void;

    canUndo?: boolean;

    undoCount?: number;

    undoHistory?: AIHistoryItem[];
};

type ChatMessage = {
    id: number;
    role: "user" | "assistant";
    content: string;
    code?: string;
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

function AIChat({
    context,
    fileName,
    onApplyCode,
    onUndoCode,
    canUndo = false,
    undoCount = 0,
    undoHistory = [],
}: AIChatProps) {
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
            context ?? "",
            previewCode,
        );
    }, [
        context,
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
        setPreviewCode(code);
        setPreviewMessageId(
            messageId,
        );
        setPreviewAction(action);
    };

    /* =====================================================
       CLOSE PREVIEW
       ===================================================== */

    const closePreview = () => {
        setPreviewCode(null);
        setPreviewMessageId(null);
        setPreviewAction("general");
    };

    /* =====================================================
       APPLY PREVIEW
       ===================================================== */

    const handleApplyPreview = () => {
        if (
            previewCode === null
        ) {
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
        closePreview();
    };

    /* =====================================================
       SEND MESSAGE
       ===================================================== */

    const handleSend = async () => {
        const message =
            input.trim();

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

        try {
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
                );

            /*
             * Send the current editor content
             * as context.
             */
            const response =
                await sendAIChat({
                    message,
                    history,
                    context,
                });

            /*
             * Extract the first Markdown
             * code block from the response.
             */
            const generatedCode =
                extractCodeBlock(
                    response.message,
                );

            const messageId =
                Date.now() +
                Math.random();

            /*
             * Add AI response to chat.
             */
            setMessages(
                (previous) => [
                    ...previous,
                    {
                        id:
                            messageId,
                        role:
                            "assistant",
                        content:
                            response.message,
                        code:
                            generatedCode ??
                            undefined,
                    },
                ],
            );

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

            addMessage(
                "assistant",
                `⚠️ ${errorMessage}`,
            );
        } finally {
            setIsLoading(false);
        }
    };

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

    /* =====================================================
       RENDER
       ===================================================== */

    return (
        <section className="ai-chat">
            {/* =================================================
                HEADER
                ================================================= */}

            <header className="ai-chat__header">
                <div className="ai-chat__title-section">
                    <div className="ai-chat__icon">
                        AI
                    </div>

                    <div>
                        <h2 className="ai-chat__title">
                            AI Assistant
                        </h2>

                        <span className="ai-chat__status">
                            <span className="ai-chat__status-dot" />

                            {isLoading
                                ? "Thinking..."
                                : "Ready"}
                        </span>

                        {fileName && (
                            <span className="ai-chat__context">
                                Context:{" "}
                                {fileName}
                            </span>
                        )}
                    </div>
                </div>

                {/* Explain */}

                <button
                    type="button"
                    className="ai-chat__action-button"
                    onClick={() =>
                        setQuickAction(
                            "explain",
                            "Explain this code step by step.",
                        )
                    }
                    disabled={
                        !context?.trim() ||
                        isLoading
                    }
                >
                    Explain Code
                </button>

                {/* Fix */}

                <button
                    type="button"
                    className="ai-chat__action-button"
                    onClick={() =>
                        setQuickAction(
                            "fix",
                            "Find the bugs in this code, explain them briefly, and provide the corrected code.",
                        )
                    }
                    disabled={
                        !context?.trim() ||
                        isLoading
                    }
                >
                    Fix Code
                </button>

                {/* Optimize */}

                <button
                    type="button"
                    className="ai-chat__action-button"
                    onClick={() =>
                        setQuickAction(
                            "optimize",
                            "Optimize this code for performance, readability, and maintainability. Explain the improvements and provide the optimized code.",
                        )
                    }
                    disabled={
                        !context?.trim() ||
                        isLoading
                    }
                >
                    Optimize
                </button>

                {/* Generate Tests */}

                <button
                    type="button"
                    className="ai-chat__action-button"
                    onClick={() =>
                        setQuickAction(
                            "tests",
                            "Generate comprehensive unit tests for this code. Cover normal cases, edge cases, and error cases. Use the appropriate testing framework for the detected language.",
                        )
                    }
                    disabled={
                        !context?.trim() ||
                        isLoading
                    }
                >
                    Generate Tests
                </button>

                <button
                    type="button"
                    className="ai-chat__menu-button"
                    aria-label="AI chat menu"
                >
                    ⋮
                </button>
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
                                        {message.content}
                                    </div>

                                    {message.role ===
                                        "assistant" &&
                                        message.code && (
                                            <div className="ai-chat__code-actions">
                                                <button
                                                    type="button"
                                                    className="ai-chat__preview-button"
                                                    onClick={() =>
                                                        handlePreviewCode(
                                                            message.code!,
                                                            message.id,
                                                            messageAction,
                                                        )
                                                    }
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
                                AI Proposed Changes
                            </span>

                            <span className="ai-chat__preview-subtitle">
                                Review changes before applying to editor
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

                    <div className="ai-chat__diff">
                        {diffLines.map(
                            (line) => (
                                <div
                                    key={
                                        line.id
                                    }
                                    className={`ai-chat__diff-line ai-chat__diff-line--${line.type}`}
                                >
                                    <span className="ai-chat__diff-old-number">
                                        {line.oldLineNumber ??
                                            ""}
                                    </span>

                                    <span className="ai-chat__diff-new-number">
                                        {line.newLineNumber ??
                                            ""}
                                    </span>

                                    <span className="ai-chat__diff-marker">
                                        {line.type ===
                                        "added"
                                            ? "+"
                                            : line.type ===
                                              "removed"
                                            ? "-"
                                            : " "}
                                    </span>

                                    <code className="ai-chat__diff-content">
                                        {line.text ||
                                            " "}
                                    </code>
                                </div>
                            ),
                        )}
                    </div>

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
                        >
                            Apply to Editor
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
                        onClick={() =>
                            void handleSend()
                        }
                        disabled={
                            !input.trim() ||
                            isLoading
                        }
                        aria-label="Send message"
                    >
                        {isLoading
                            ? "…"
                            : "↑"}
                    </button>
                </div>

                <div className="ai-chat__hint">
                    <span>
                        Enter
                    </span>{" "}
                    to send ·{" "}
                    <span>
                        Shift + Enter
                    </span>{" "}
                    for new line
                </div>
            </div>
        </section>
    );
}

export default AIChat;