import { useState } from "react";
import { Play, Trash2 } from "lucide-react";

import { runProjectFile } from "../../services/terminalService";

interface TerminalProps {
    projectId: number;
    fileId: number | null;
    fileName: string | null;
    code: string | null;
}

function getErrorMessage(error: any): string {
    if (error?.response?.data?.detail) {
        return String(error.response.data.detail);
    }
    if (error?.message) {
        return String(error.message);
    }
    return "Sandbox execution failed.";
}

export default function Terminal({
    projectId,
    fileId,
    fileName,
    code,
}: TerminalProps) {
    const [output, setOutput] = useState<string[]>([
        "AI Coding IDE Terminal",
        "",
    ]);
    const [isRunning, setIsRunning] = useState(false);

    async function handleRun() {
        if (fileId === null || code === null || !fileName || isRunning) {
            return;
        }

        setIsRunning(true);
        setOutput((current) => [...current, `Run ${fileName}`]);

        try {
            const result = await runProjectFile(projectId, fileId, code);
            setOutput((current) => [
                ...current,
                ...(result.stdout ? [result.stdout] : []),
                ...(result.stderr ? [result.stderr] : []),
                `Process exited with code ${result.exit_code}`,
            ]);
        } catch (error: unknown) {
            setOutput((current) => [
                ...current,
                `Error: ${getErrorMessage(error)}`,
            ]);
        } finally {
            setIsRunning(false);
        }
    }

    return (
        <div className="terminal">
            <div className="terminal-header">
                <span>Terminal</span>
                <div className="terminal-actions">
                    <button
                        type="button"
                        onClick={handleRun}
                        disabled={isRunning || fileId === null || code === null}
                        title={fileName ? `Run ${fileName} in an isolated container` : "Select a source file to run"}
                    >
                        <Play size={13} aria-hidden="true" />
                        {isRunning ? "Running" : "Run"}
                    </button>
                    <button
                        type="button"
                        onClick={() => setOutput(["AI Coding IDE Terminal", ""])}
                        disabled={isRunning}
                        title="Clear terminal output"
                    >
                        <Trash2 size={13} aria-hidden="true" />
                        Clear
                    </button>
                </div>
            </div>

            <div className="terminal-output" aria-live="polite">
                {output.map((line, index) => (
                    <div key={index} className="terminal-line">
                        {line}
                    </div>
                ))}
            </div>

            <div className="terminal-input-row">
                <span className="terminal-prompt">RUN</span>
                <span className="terminal-active-file">
                    {fileName ?? "Select a source file"}
                    {isRunning ? " · Running in sandbox…" : " · Current editor contents"}
                </span>
            </div>
        </div>
    );
}