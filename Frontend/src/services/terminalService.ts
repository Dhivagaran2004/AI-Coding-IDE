import axios from "axios";


// =========================================
// Terminal Request
// =========================================

export interface TerminalExecuteRequest {

    command: string;
}


// =========================================
// Terminal Response
// =========================================

export interface TerminalExecuteResponse {

    exit_code: number;

    stdout: string;

    stderr: string;

    success: boolean;
}


// =========================================
// Stop Response
// =========================================

export interface TerminalStopResponse {

    exit_code: number;

    stdout: string;

    stderr: string;

    success: boolean;

    stopped: boolean;
}


// =========================================
// Execute Terminal Command
// =========================================

export async function executeTerminalCommand(
    projectId: number,
    command: string
): Promise<TerminalExecuteResponse> {

    const response =
        await axios.post<TerminalExecuteResponse>(
            `http://127.0.0.1:8000/projects/${projectId}/terminal/execute`,
            {
                command: command,
            }
        );


    return response.data;
}


// =========================================
// Stop Terminal Command
// =========================================

export async function stopTerminalCommand(
    projectId: number
): Promise<TerminalStopResponse> {

    const response =
        await axios.post<TerminalStopResponse>(
            `http://127.0.0.1:8000/projects/${projectId}/terminal/stop`
        );


    return response.data;
}