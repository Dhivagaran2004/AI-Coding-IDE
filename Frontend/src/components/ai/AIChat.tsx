import { useState, useRef, useEffect } from "react";

import "./AIChat.css";

import {
    sendAIChat,
    type AIMessage,
} from "../../services/aiService";

type AIChatProps = {
    context?: string | null;
    fileName?: string | null;
    onApplyCode?: (code: string) => void;
};

type ChatMessage = {
    id: number;
    role: "user" | "assistant";
    content: string;
    code?: string;
};

function extractCodeBlock(
    content: string,
): string | null {
    const match = content.match(
        /```(?:[a-zA-Z0-9_+-]+)?\s*([\s\S]*?)```/,
    );

    if (!match) {
        return null;
    }

    return match[1].trim();
}

function AIChat({
    context,
    fileName,
    onApplyCode,
}: AIChatProps) {
    const [input, setInput] = useState("");

    const [messages, setMessages] = useState<ChatMessage[]>([
        {
            id: 1,
            role: "assistant",
            content:
                "Hello! I'm your AI coding assistant. Ask me about your code, debugging, or development tasks.",
        },
    ]);

    const [isLoading, setIsLoading] = useState(false);

    const inputRef = useRef<HTMLTextAreaElement | null>(null);
    const messagesEndRef = useRef<HTMLDivElement | null>(null);

    // Auto-scroll to bottom of messages
    const scrollToBottom = () => {
        messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
    };

    useEffect(() => {
        scrollToBottom();
    }, [messages, isLoading]);

    // Auto-focus input when loading finishes
    useEffect(() => {
        if (!isLoading) {
            inputRef.current?.focus();
        }
    }, [isLoading]);

    const addMessage = (
        role: "user" | "assistant",
        content: string,
        code?: string,
    ) => {
        setMessages((previous) => [
            ...previous,
            {
                id: Date.now() + Math.random(),
                role,
                content,
                code,
            },
        ]);
    };

    const handleSend = async () => {
        const message = input.trim();

        if (!message || isLoading) {
            return;
        }

        /*
         * Add user message immediately.
         */
        addMessage("user", message);

        setInput("");
        setIsLoading(true);

        try {
            /*
             * Convert frontend messages into
             * backend conversation history.
             */
            const history: AIMessage[] = messages.map(
                (item) => ({
                    role: item.role,
                    content: item.content,
                }),
            );

            /*
             * Send request to FastAPI.
             */
            const response = await sendAIChat({
                message,
                history,
                context,
            });

            /*
             * Display AI response.
             */
            const generatedCode = extractCodeBlock(response.message);

            addMessage(
                "assistant",
                response.message,
                generatedCode ?? undefined,
            );
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

    return (
        <section className="ai-chat">

            {/* Header */}
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
                            {isLoading ? "Thinking..." : "Ready"}

                        </span>

                        {fileName && (
                            <span className="ai-chat__context">
                                Context: {fileName}
                            </span>
                        )}

                    </div>

                    <button
                        type="button"
                        className="ai-chat__action-button"
                        onClick={() => {
                            if (context?.trim()) {
                                setInput(
                                    "Explain this code step by step."
                                );
                                inputRef.current?.focus();
                            }
                        }}
                        disabled={!context?.trim() || isLoading}
                    >
                        Explain Code
                    </button>
                </div>
                <button
                    type="button"
                    className="ai-chat__action-button"
                    onClick={() => {
                        if (context?.trim()) {
                            setInput(
                                "Find the bugs in this code, explain them briefly, and provide the corrected code."
                            );
                            inputRef.current?.focus();
                        }
                    }}
                    disabled={!context?.trim() || isLoading}
                >
                    Fix Code
                </button>

                <button
                    type="button"
                    className="ai-chat__menu-button"
                    aria-label="AI chat menu"
                >
                    ⋮
                </button>

            </header >

            {/* Messages */}
            < div className="ai-chat__messages" >

                {
                    messages.map((message) => (

                        <div
                            key={message.id}
                            className={`ai-chat__message-row ai-chat__message-row--${message.role}`}
                        >

                            <div
                                className={`ai-chat__avatar ai-chat__avatar--${message.role}`}
                            >
                                {message.role === "assistant"
                                    ? "AI"
                                    : "You"}
                            </div>

                            <div
                                className={`ai-chat__bubble ai-chat__bubble--${message.role}`}
                            >
                                {message.content}

                                {message.role === "assistant" &&
                                    message.code && (
                                        <button
                                            type="button"
                                            className="ai-chat__apply-button"
                                            onClick={() => {
                                                onApplyCode?.(message.code!);
                                            }}
                                        >
                                            Apply to Editor
                                        </button>
                                    )}
                            </div>
                        </div>

                    ))
                }

                {/* Loading indicator */}
                {
                    isLoading && (

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

                    )
                }

                <div ref={messagesEndRef} />

            </div >

            {/* Input */}
            < div className="ai-chat__input-area" >

                <div className="ai-chat__input-wrapper">

                    <textarea
                        ref={inputRef}
                        className="ai-chat__input"
                        value={input}
                        onChange={(event) =>
                            setInput(event.target.value)
                        }
                        onKeyDown={handleKeyDown}
                        placeholder={
                            isLoading
                                ? "AI is thinking..."
                                : "Ask AI about your code..."
                        }
                        rows={1}
                        disabled={isLoading}
                    />

                    <button
                        type="button"
                        className="ai-chat__send-button"
                        onClick={() => void handleSend()}
                        disabled={
                            !input.trim() ||
                            isLoading
                        }
                        aria-label="Send message"
                    >
                        {isLoading ? "…" : "↑"}
                    </button>

                </div>

                <div className="ai-chat__hint">
                    <span>Enter</span> to send ·{" "}
                    <span>Shift + Enter</span> for new line
                </div>

            </div >

        </section >
    );
}

export default AIChat;