import api from "./api";

export interface TerminalExecutionResponse {
    exit_code: number;
    stdout: string;
    stderr: string;
    success: boolean;
}

export async function runProjectFile(
    projectId: number,
    fileId: number,
    code: string,
): Promise<TerminalExecutionResponse> {
    const response = await api.post<TerminalExecutionResponse>(
        `/projects/${projectId}/terminal/run`,
        { file_id: fileId, code },
        { timeout: 30000 },
    );
    return response.data;
}