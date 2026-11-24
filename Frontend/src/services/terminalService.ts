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

    executed?: boolean;
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

export type InteractiveTerminalShell = "powershell" | "cmd" | "bash";

export interface InteractiveTerminalSession {
    id: string;
    project_id: number;
    shell: InteractiveTerminalShell;
    name: string;
    cwd: string;
    created_at: number;
    running: boolean;
    ticket?: string;
}

interface TerminalTicketResponse {
    ticket: string;
}

export interface WorkspaceFileChange {
    path: string;
    file_id?: number;
    content?: string;
    deleted?: boolean;
    directory?: boolean;
}

interface WorkspaceSyncResponse {
    changes: WorkspaceFileChange[];
    skipped: Array<{ path: string; reason: string }>;
}

export async function syncInteractiveTerminalWorkspace(
    projectId: number,
): Promise<WorkspaceSyncResponse> {
    const response = await api.post<WorkspaceSyncResponse>(
        `/projects/${projectId}/workspace/sync`,
    );
    return response.data;
}

export async function listInteractiveTerminalSessions(
    projectId: number,
): Promise<InteractiveTerminalSession[]> {
    const response = await api.get<InteractiveTerminalSession[]>(
        `/projects/${projectId}/terminal/sessions`,
    );
    return response.data;
}

export async function createInteractiveTerminalSession(
    projectId: number,
    shell: InteractiveTerminalShell,
): Promise<InteractiveTerminalSession> {
    const response = await api.post<InteractiveTerminalSession>(
        `/projects/${projectId}/terminal/sessions`,
        { shell },
    );
    return response.data;
}

export async function getInteractiveTerminalTicket(
    projectId: number,
    sessionId: string,
): Promise<string> {
    const response = await api.post<TerminalTicketResponse>(
        `/projects/${projectId}/terminal/sessions/${sessionId}/connect`,
    );
    return response.data.ticket;
}

export async function renameInteractiveTerminalSession(
    projectId: number,
    sessionId: string,
    name: string,
): Promise<InteractiveTerminalSession> {
    const response = await api.put<InteractiveTerminalSession>(
        `/projects/${projectId}/terminal/sessions/${sessionId}`,
        { name },
    );
    return response.data;
}

export async function closeInteractiveTerminalSession(
    projectId: number,
    sessionId: string,
): Promise<void> {
    await api.delete(`/projects/${projectId}/terminal/sessions/${sessionId}`);
}

export function createInteractiveTerminalSocket(
    projectId: number,
    sessionId: string,
    ticket: string,
): WebSocket {
    const url = new URL(
        api.defaults.baseURL || window.location.origin,
        window.location.origin,
    );
    url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
    url.pathname = `/projects/${projectId}/terminal/sessions/${sessionId}/ws`;
    url.search = new URLSearchParams({ ticket }).toString();
    return new WebSocket(url);
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