import {
    useState,
    type KeyboardEvent,
} from "react";

import {
    executeTerminalCommand,
    stopTerminalCommand,
} from "../../services/terminalService";


// =========================================
// Props
// =========================================

interface TerminalProps {
    projectId: number;
}


// =========================================
// Error Helper
// =========================================

function getErrorMessage(
    error: any,
    fallbackMessage: string
): string {

    // Axios response error

    if (error?.response?.data?.detail) {

        return String(
            error.response.data.detail
        );
    }


    // Axios response without detail

    if (error?.response?.status) {

        const status =
            error.response.status;

        if (status === 400) {

            return "Invalid terminal request.";
        }

        if (status === 404) {

            return "Project workspace was not found.";
        }

        if (status === 500) {

            return "Terminal server error.";
        }

        return `Terminal request failed with HTTP ${status}.`;
    }


    // Network error

    if (
        error?.message ===
        "Network Error"
    ) {

        return "Unable to connect to the terminal server.";
    }


    // JavaScript error

    if (error?.message) {

        return String(
            error.message
        );
    }


    return fallbackMessage;
}


// =========================================
// Terminal Component
// =========================================

export default function Terminal({
    projectId,
}: TerminalProps) {

    // =========================================
    // Command Input
    // =========================================

    const [
        command,
        setCommand,
    ] = useState("");


    // =========================================
    // Terminal Output
    // =========================================

    const [
        output,
        setOutput,
    ] = useState<string[]>([
        "AI Coding IDE Terminal",
        "",
    ]);


    // =========================================
    // Command History
    // =========================================

    const [
        commandHistory,
        setCommandHistory,
    ] = useState<string[]>([]);


    // =========================================
    // Current History Position
    // =========================================

    const [
        historyIndex,
        setHistoryIndex,
    ] = useState(-1);


    // =========================================
    // Loading State
    // =========================================

    const [
        isRunning,
        setIsRunning,
    ] = useState(false);


    // =========================================
    // Stop Running Process
    // =========================================

    async function handleStopCommand() {

        if (!isRunning) {

            return;
        }


        try {

            console.log(
                "Stopping terminal command:",
                projectId
            );


            const result =
                await stopTerminalCommand(
                    projectId
                );


            console.log(
                "Terminal stop response:",
                result
            );


            setOutput(
                current => [
                    ...current,
                    "",
                    "Process stopped.",
                    "",
                ]
            );


        } catch (error: any) {

            console.error(
                "Failed to stop terminal command:",
                error
            );


            const message =
                getErrorMessage(
                    error,
                    "Failed to stop command."
                );


            setOutput(
                current => [
                    ...current,
                    `Error stopping process: ${message}`,
                ]
            );


        } finally {

            setIsRunning(false);
        }
    }


    // =========================================
    // Execute Command
    // =========================================

    async function handleExecuteCommand() {

        const trimmedCommand =
            command.trim();


        // =====================================
        // Empty Command
        // =====================================

        if (!trimmedCommand) {

            setOutput(
                current => [
                    ...current,
                    "Error: Command cannot be empty.",
                ]
            );

            return;
        }


        // =====================================
        // Add Command To History
        // =====================================

        setCommandHistory(
            currentHistory => {

                // Avoid duplicate consecutive commands

                if (
                    currentHistory[
                    currentHistory.length - 1
                    ] === trimmedCommand
                ) {

                    return currentHistory;
                }


                return [
                    ...currentHistory,
                    trimmedCommand,
                ];
            }
        );


        // Reset history position

        setHistoryIndex(-1);


        // =====================================
        // Show Command
        // =====================================

        setOutput(
            current => [
                ...current,
                `PS> ${trimmedCommand}`,
            ]
        );


        // Clear input

        setCommand("");


        // Start loading

        setIsRunning(true);


        try {

            console.log(
                "Executing terminal command:",
                {
                    projectId,
                    command: trimmedCommand,
                }
            );


            const result =
                await executeTerminalCommand(
                    projectId,
                    trimmedCommand
                );


            console.log(
                "Terminal response:",
                result
            );


            // =====================================
            // stdout
            // =====================================

            if (result.stdout) {

                setOutput(
                    current => [
                        ...current,
                        result.stdout,
                    ]
                );
            }


            // =====================================
            // stderr
            // =====================================

            if (result.stderr) {

                setOutput(
                    current => [
                        ...current,
                        result.stderr,
                    ]
                );
            }


            // =====================================
            // Exit Code
            // =====================================

            if (
                result.exit_code !== 0
            ) {

                setOutput(
                    current => [
                        ...current,
                        `Process exited with code ${result.exit_code}`,
                    ]
                );

            } else {

                // Successful command with no output

                if (
                    !result.stdout &&
                    !result.stderr
                ) {

                    setOutput(
                        current => [
                            ...current,
                            "Command completed successfully.",
                        ]
                    );
                }
            }


        } catch (error: any) {

            console.error(
                "Terminal command failed:",
                error
            );


            const message =
                getErrorMessage(
                    error,
                    "Failed to execute command."
                );


            setOutput(
                current => [
                    ...current,
                    `Error: ${message}`,
                ]
            );


        } finally {

            setIsRunning(false);
        }
    }


    // =========================================
    // Keyboard Handling
    // =========================================

    function handleKeyDown(
        event: KeyboardEvent<HTMLInputElement>
    ) {

        // =====================================
        // Enter
        // =====================================

        if (
            event.key === "Enter"
        ) {

            event.preventDefault();


            if (!isRunning) {

                handleExecuteCommand();
            }


            return;
        }


        // =====================================
        // Arrow Up
        // =====================================

        if (
            event.key === "ArrowUp"
        ) {

            event.preventDefault();


            if (
                commandHistory.length === 0
            ) {

                return;
            }


            const newIndex =
                historyIndex === -1
                    ? commandHistory.length - 1
                    : Math.max(
                        0,
                        historyIndex - 1
                    );


            setHistoryIndex(
                newIndex
            );


            setCommand(
                commandHistory[newIndex]
            );


            return;
        }


        // =====================================
        // Arrow Down
        // =====================================

        if (
            event.key === "ArrowDown"
        ) {

            event.preventDefault();


            if (
                commandHistory.length === 0
            ) {

                return;
            }


            if (
                historyIndex === -1
            ) {

                return;
            }


            const newIndex =
                historyIndex + 1;


            if (
                newIndex >=
                commandHistory.length
            ) {

                setHistoryIndex(-1);

                setCommand("");

                return;
            }


            setHistoryIndex(
                newIndex
            );


            setCommand(
                commandHistory[newIndex]
            );
        }
    }


    // =========================================
    // Clear Terminal
    // =========================================

    function handleClear() {

        setOutput([
            "AI Coding IDE Terminal",
            "",
        ]);


        setCommandHistory([]);


        setHistoryIndex(-1);


        setCommand("");
    }


    // =========================================
    // UI
    // =========================================

    return (

        <div className="terminal">

            {/* ================================= */}
            {/* Terminal Header */}
            {/* ================================= */}

            <div className="terminal-header">

                <span>
                    Terminal
                </span>


                <div className="terminal-actions">

                    {isRunning && (

                        <button
                            type="button"
                            onClick={
                                handleStopCommand
                            }
                        >
                            Stop
                        </button>

                    )}


                    <button
                        type="button"
                        onClick={
                            handleClear
                        }
                        disabled={isRunning}
                    >
                        Clear
                    </button>

                </div>

            </div>


            {/* ================================= */}
            {/* Terminal Output */}
            {/* ================================= */}

            <div className="terminal-output">

                {output.map(
                    (line, index) => (

                        <div
                            key={index}
                            className="terminal-line"
                        >
                            {line}
                        </div>

                    )
                )}

            </div>


            {/* ================================= */}
            {/* Terminal Input */}
            {/* ================================= */}

            <div className="terminal-input-row">

                <span className="terminal-prompt">
                    PS&gt;
                </span>


                <input
                    type="text"
                    value={command}
                    onChange={event => {

                        setCommand(
                            event.target.value
                        );


                        setHistoryIndex(-1);
                    }}
                    onKeyDown={
                        handleKeyDown
                    }
                    placeholder={
                        isRunning
                            ? "Command running..."
                            : "Enter command..."
                    }
                    disabled={isRunning}
                    autoComplete="off"
                    spellCheck={false}
                />

            </div>

        </div>
    );
}