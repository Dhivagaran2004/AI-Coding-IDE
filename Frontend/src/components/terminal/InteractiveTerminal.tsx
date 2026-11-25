import {
    forwardRef,
    useCallback,
    useEffect,
    useImperativeHandle,
    useRef,
    useState,
} from "react";
import { isAxiosError } from "axios";
import { FitAddon } from "@xterm/addon-fit";
import { WebLinksAddon } from "@xterm/addon-web-links";
import { Terminal as XTerm } from "@xterm/xterm";
import "@xterm/xterm/css/xterm.css";
import {
    Eraser,
    Maximize2,
    Minimize2,
    Pencil,
    Plus,
    Square,
    X,
} from "lucide-react";
import {
    closeInteractiveTerminalSession,
    createInteractiveTerminalSession,
    createInteractiveTerminalSocket,
    getInteractiveTerminalTicket,
    listInteractiveTerminalSessions,
    renameInteractiveTerminalSession,
    syncInteractiveTerminalWorkspace,
    type InteractiveTerminalSession,
    type InteractiveTerminalShell,
    type WorkspaceFileChange,
} from "../../services/terminalService";
import type { TerminalHandle } from "./Terminal";

interface InteractiveTerminalProps {
    projectId: number;
    onMinimize: () => void;
    onToggleMaximize: () => void;
    isMaximized: boolean;
    onWorkspaceChange: (changes: WorkspaceFileChange[]) => void;
}

interface XTermHandle {
    focus: () => void;
    fit: () => void;
    clear: () => void;
    interrupt: () => void;
}

type TerminalMessage =
    | { type: "output"; data: string }
    | { type: "error"; message: string }
    | { type: "pong" };

function terminalErrorMessage(error: unknown, fallback: string): string {
    if (isAxiosError<{ detail?: unknown }>(error)) {
        const detail = error.response?.data?.detail;
        if (typeof detail === "string") {
            return detail;
        }
    }
    return error instanceof Error ? error.message : fallback;
}

function defaultShell(): InteractiveTerminalShell {
    return navigator.userAgent.includes("Windows") ? "powershell" : "bash";
}

function TerminalProcess({
    projectId,
    session,
    active,
    onReady,
}: {
    projectId: number;
    session: InteractiveTerminalSession;
    active: boolean;
    onReady: (sessionId: string, handle: XTermHandle | null) => void;
}) {
    const hostRef = useRef<HTMLDivElement | null>(null);
    const terminalHandle = useRef<XTermHandle | null>(null);

    useEffect(() => {
        const host = hostRef.current;
        if (!host) return;

        const terminal = new XTerm({
            allowProposedApi: false,
            cursorBlink: true,
            fontFamily: 'Consolas, "Cascadia Mono", "Courier New", monospace',
            fontSize: 13,
            scrollback: 10000,
            theme: {
                background: "#1e1e1e",
                foreground: "#cccccc",
                cursor: "#aeafad",
                selectionBackground: "#264f78",
            },
        });
        const fitAddon = new FitAddon();
        terminal.loadAddon(fitAddon);
        terminal.loadAddon(new WebLinksAddon());
        terminal.open(host);

        let socket: WebSocket | null = null;
        let disposed = false;
        let reconnectTimer = 0;
        let retryCount = 0;
        let pendingTicket = session.ticket;
        let resizeFrame = 0;

        const fitAndResize = () => {
            if (disposed || host.clientWidth === 0 || host.clientHeight === 0) {
                return;
            }
            fitAddon.fit();
            if (socket?.readyState === WebSocket.OPEN) {
                socket.send(JSON.stringify({
                    type: "resize",
                    columns: terminal.cols,
                    rows: terminal.rows,
                }));
            }
        };

        const resizeObserver = new ResizeObserver(() => {
            cancelAnimationFrame(resizeFrame);
            resizeFrame = requestAnimationFrame(fitAndResize);
        });
        resizeObserver.observe(host);

        const dataSubscription = terminal.onData((data) => {
            if (socket?.readyState === WebSocket.OPEN) {
                socket.send(JSON.stringify({ type: "input", data }));
            }
        });
        const pasteText = (text: string) => {
            if (socket?.readyState === WebSocket.OPEN) {
                socket.send(JSON.stringify({ type: "input", data: text }));
            }
        };
        const reportClipboardError = () => {
            terminal.write("\r\n\x1b[31mClipboard access was denied by the browser.\x1b[0m\r\n");
        };
        const keyHandler = (event: KeyboardEvent) => {
            if (event.type !== "keydown" || !(event.ctrlKey || event.metaKey)) {
                return true;
            }
            if (event.key.toLowerCase() === "c" && terminal.hasSelection()) {
                if (!navigator.clipboard?.writeText) {
                    reportClipboardError();
                    return false;
                }
                void navigator.clipboard.writeText(terminal.getSelection())
                    .catch(reportClipboardError);
                return false;
            }
            if (event.key.toLowerCase() === "v") {
                if (!navigator.clipboard?.readText) {
                    reportClipboardError();
                    return false;
                }
                void navigator.clipboard.readText()
                    .then(pasteText)
                    .catch(reportClipboardError);
                return false;
            }
            return true;
        };
        terminal.attachCustomKeyEventHandler(keyHandler);
        const handlePaste = (event: ClipboardEvent) => {
            const text = event.clipboardData?.getData("text");
            if (text) {
                event.preventDefault();
                pasteText(text);
            }
        };
        host.addEventListener("paste", handlePaste);

        const connect = async () => {
            try {
                const ticket = pendingTicket ??
                    await getInteractiveTerminalTicket(projectId, session.id);
                pendingTicket = undefined;
                if (disposed) return;

                socket = createInteractiveTerminalSocket(
                    projectId,
                    session.id,
                    ticket,
                );
                socket.onopen = () => {
                    retryCount = 0;
                    fitAndResize();
                };
                socket.onmessage = (event: MessageEvent<string>) => {
                    const message = JSON.parse(event.data) as TerminalMessage;
                    if (message.type === "output") {
                        terminal.write(message.data);
                    } else if (message.type === "error") {
                        terminal.write(`\r\n\x1b[31m${message.message}\x1b[0m\r\n`);
                    }
                };
                socket.onclose = (event) => {
                    socket = null;
                    if (event.code === 1000) {
                        terminal.write("\r\n\x1b[90m[shell exited]\x1b[0m\r\n");
                        return;
                    }
                    if (!disposed) {
                        const delay = Math.min(1000 * (2 ** retryCount), 15000);
                        retryCount += 1;
                        reconnectTimer = window.setTimeout(() => void connect(), delay);
                    }
                };
                socket.onerror = () => socket?.close();
            } catch (error) {
                if (!disposed) {
                    terminal.write(
                        `\r\n\x1b[31m${terminalErrorMessage(error, "Terminal connection failed.")}\x1b[0m\r\n`,
                    );
                    reconnectTimer = window.setTimeout(() => void connect(), 5000);
                }
            }
        };

        const handle: XTermHandle = {
            focus: () => terminal.focus(),
            fit: fitAndResize,
            clear: () => terminal.clear(),
            interrupt: () => {
                if (socket?.readyState === WebSocket.OPEN) {
                    socket.send(JSON.stringify({ type: "input", data: "\u0003" }));
                }
            },
        };
        terminalHandle.current = handle;
        onReady(session.id, handle);
        void connect();

        return () => {
            disposed = true;
            window.clearTimeout(reconnectTimer);
            cancelAnimationFrame(resizeFrame);
            resizeObserver.disconnect();
            dataSubscription.dispose();
            host.removeEventListener("paste", handlePaste);
            terminalHandle.current = null;
            onReady(session.id, null);
            socket?.close();
            terminal.dispose();
        };
    }, [onReady, projectId, session.id, session.ticket]);

    useEffect(() => {
        if (active) {
            requestAnimationFrame(() => {
                terminalHandle.current?.fit();
                terminalHandle.current?.focus();
            });
        }
    }, [active]);

    return (
        <div
            ref={hostRef}
            className="interactive-terminal-screen"
            aria-label={`${session.name} terminal`}
            style={{ display: active ? "block" : "none" }}
        />
    );
}

const InteractiveTerminal = forwardRef<TerminalHandle, InteractiveTerminalProps>(
    function InteractiveTerminal({
        projectId,
        onMinimize,
        onToggleMaximize,
        isMaximized,
        onWorkspaceChange,
    }, ref) {
        const [sessions, setSessions] = useState<InteractiveTerminalSession[]>([]);
        const [activeId, setActiveId] = useState<string | null>(null);
        const [shell, setShell] = useState<InteractiveTerminalShell>(defaultShell);
        const [error, setError] = useState("");
        const handles = useRef(new Map<string, XTermHandle>());
        const initialization = useRef<{
            projectId: number;
            promise: Promise<InteractiveTerminalSession[]>;
        } | null>(null);

        const registerHandle = useCallback(
            (sessionId: string, handle: XTermHandle | null) => {
                if (handle) handles.current.set(sessionId, handle);
                else handles.current.delete(sessionId);
            },
            [],
        );

        useImperativeHandle(ref, () => ({
            focus: () => {
                if (activeId) handles.current.get(activeId)?.focus();
            },
        }), [activeId]);

        useEffect(() => {
            let active = true;
            if (initialization.current?.projectId !== projectId) {
                const promise = listInteractiveTerminalSessions(projectId)
                    .then(async (items) => items.length > 0
                        ? items
                        : [await createInteractiveTerminalSession(
                            projectId,
                            defaultShell(),
                        )]);
                initialization.current = { projectId, promise };
            }
            void initialization.current.promise
                .then((initialSessions) => {
                    if (!active) return;
                    setSessions(initialSessions);
                    setActiveId(initialSessions[0]?.id ?? null);
                    setError("");
                })
                .catch((cause: unknown) => {
                    if (active) {
                        setError(terminalErrorMessage(
                            cause,
                            "Unable to load terminal sessions.",
                        ));
                    }
                });
            return () => {
                active = false;
            };
        }, [projectId]);

        useEffect(() => {
            let active = true;
            let inFlight = false;
            const synchronize = async () => {
                if (inFlight) return;
                inFlight = true;
                try {
                    const result = await syncInteractiveTerminalWorkspace(projectId);
                    if (!active) return;
                    if (result.changes.length) {
                        onWorkspaceChange(result.changes);
                    }
                    if (result.skipped.length) {
                        setError(
                            `Some workspace files were not imported: ${result.skipped
                                .map((item) => item.path)
                                .join(", ")}`,
                        );
                    }
                } catch (cause) {
                    if (active) {
                        setError(terminalErrorMessage(
                            cause,
                            "Workspace synchronization failed.",
                        ));
                    }
                } finally {
                    inFlight = false;
                }
            };
            void synchronize();
            const timer = window.setInterval(() => void synchronize(), 3000);
            return () => {
                active = false;
                window.clearInterval(timer);
            };
        }, [onWorkspaceChange, projectId]);

        const createSession = async () => {
            try {
                const session = await createInteractiveTerminalSession(projectId, shell);
                setSessions((current) => [...current, session]);
                setActiveId(session.id);
                setError("");
            } catch (cause) {
                setError(terminalErrorMessage(
                    cause,
                    "Unable to create terminal session.",
                ));
            }
        };

        const renameSession = async (session: InteractiveTerminalSession) => {
            const name = window.prompt("Terminal name", session.name)?.trim();
            if (!name || name === session.name) return;
            try {
                const updated = await renameInteractiveTerminalSession(
                    projectId,
                    session.id,
                    name,
                );
                setSessions((current) =>
                    current.map((item) => item.id === updated.id ? updated : item),
                );
                setError("");
            } catch (cause) {
                setError(terminalErrorMessage(cause, "Unable to rename terminal."));
            }
        };

        const closeSession = async (sessionId: string) => {
            try {
                await closeInteractiveTerminalSession(projectId, sessionId);
                setSessions((current) => current.filter((item) => item.id !== sessionId));
                if (activeId === sessionId) {
                    const remaining = sessions.filter((item) => item.id !== sessionId);
                    setActiveId(remaining[0]?.id ?? null);
                }
                setError("");
            } catch (cause) {
                setError(terminalErrorMessage(
                    cause,
                    "Unable to close terminal session.",
                ));
            }
        };

        const activeSession = sessions.find((session) => session.id === activeId);

        return (
            <div className="interactive-terminal">
                <div className="interactive-terminal-toolbar">
                    <div className="interactive-terminal-heading">
                        <div className="interactive-terminal-panel-tabs">
                            <span className="interactive-terminal-title">
                                Terminal
                            </span>
                        </div>
                        <div
                            className="interactive-terminal-tabs"
                            role="tablist"
                            aria-label="Terminal sessions"
                        >
                            {sessions.map((session) => (
                                <div
                                    className={`interactive-terminal-tab${activeId === session.id ? " is-active" : ""}`}
                                    key={session.id}
                                    role="presentation"
                                >
                                    <button
                                        type="button"
                                        role="tab"
                                        aria-selected={activeId === session.id}
                                        onClick={() => setActiveId(session.id)}
                                        onDoubleClick={() => void renameSession(session)}
                                        title={`${session.name} (${session.shell}) - double-click to rename`}
                                    >
                                        {session.name}
                                    </button>
                                    <button
                                        type="button"
                                        className="interactive-terminal-tab-close"
                                        aria-label={`Close ${session.name}`}
                                        onClick={() => void closeSession(session.id)}
                                    >
                                        <X size={13} aria-hidden="true" />
                                    </button>
                                </div>
                            ))}
                        </div>
                    </div>
                    <div className="interactive-terminal-actions">
                        <select
                            className="interactive-terminal-shell"
                            aria-label="Terminal shell"
                            value={shell}
                            onChange={(event) =>
                                setShell(event.target.value as InteractiveTerminalShell)
                            }
                        >
                            <option value="powershell">PowerShell</option>
                            <option value="cmd">Command Prompt</option>
                            <option value="bash">Bash</option>
                        </select>
                        <button
                            type="button"
                            className="interactive-terminal-icon-button"
                            onClick={() => void createSession()}
                            title="New terminal"
                            aria-label="New terminal"
                        >
                            <Plus size={16} aria-hidden="true" />
                        </button>
                        <span className="interactive-terminal-action-divider" />
                        <button
                            type="button"
                            className="interactive-terminal-icon-button"
                            onClick={() => activeSession && void renameSession(activeSession)}
                            disabled={!activeSession}
                            title="Rename terminal"
                            aria-label="Rename terminal"
                        >
                            <Pencil size={14} aria-hidden="true" />
                        </button>
                        <button
                            type="button"
                            className="interactive-terminal-icon-button"
                            onClick={() => activeId && handles.current.get(activeId)?.clear()}
                            disabled={!activeSession}
                            title="Clear terminal"
                            aria-label="Clear terminal"
                        >
                            <Eraser size={15} aria-hidden="true" />
                        </button>
                        <button
                            type="button"
                            className="interactive-terminal-icon-button"
                            onClick={() => activeId && handles.current.get(activeId)?.interrupt()}
                            disabled={!activeSession}
                            title="Interrupt foreground process (Ctrl+C)"
                            aria-label="Interrupt foreground process"
                        >
                            <Square size={13} aria-hidden="true" />
                        </button>
                        <span className="interactive-terminal-action-divider" />
                        <button
                            type="button"
                            className="interactive-terminal-icon-button"
                            onClick={onToggleMaximize}
                            title={isMaximized ? "Restore terminal panel" : "Maximize terminal panel"}
                            aria-label={isMaximized ? "Restore terminal panel" : "Maximize terminal panel"}
                        >
                            {isMaximized
                                ? <Minimize2 size={15} aria-hidden="true" />
                                : <Maximize2 size={15} aria-hidden="true" />}
                        </button>
                        <button
                            type="button"
                            className="interactive-terminal-icon-button"
                            onClick={onMinimize}
                            title="Hide terminal panel"
                            aria-label="Hide terminal panel"
                        >
                            <X size={16} aria-hidden="true" />
                        </button>
                    </div>
                </div>
                {error && <div className="interactive-terminal-error" role="alert">{error}</div>}
                <div className="interactive-terminal-content">
                    {sessions.map((session) => (
                        <TerminalProcess
                            key={session.id}
                            projectId={projectId}
                            session={session}
                            active={activeId === session.id}
                            onReady={registerHandle}
                        />
                    ))}
                    {sessions.length === 0 && (
                        <div className="interactive-terminal-empty">
                            Waiting for a terminal session...
                        </div>
                    )}
                </div>
            </div>
        );
    },
);

export default InteractiveTerminal;
