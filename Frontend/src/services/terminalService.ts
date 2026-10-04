import api from "./api";


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

export type TerminalAIContext = TerminalExecuteResponse & {
    command: string;
};


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

    const response = await api.post<TerminalExecuteResponse>(
        `/projects/${projectId}/terminal/execute`,
        { command },
    );


    return response.data;
}


// =========================================
// Stop Terminal Command
// =========================================

export async function stopTerminalCommand(
    projectId: number
): Promise<TerminalStopResponse> {

    const response = await api.post<TerminalStopResponse>(
        `/projects/${projectId}/terminal/stop`,
    );


    return response.data;
}