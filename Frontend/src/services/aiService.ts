import api from "./api";
import type { TerminalAIContext } from "./terminalService";

export type CodeAction = {
    type: "code_change";
    operation: "replace" | "insert" | "delete" | "create_file";
    file_path: string;
    description: string;
    start_line?: number | null;
    end_line?: number | null;
    old_code: string;
    new_code: string;
};

export type AIMessage = {
    role: "system" | "user" | "assistant";
    content: string;
};

export type AIChatRequest = {
    message: string;
    context?: string | null;
    selected_code?: {
        file_path: string;
        language: string;
        code: string;
        start_line: number;
        end_line: number;
    } | null;
    terminal_context?: TerminalAIContext | null;
    history?: AIMessage[];
    project_id?: number;
};

export type AIChatResponse = {
    message: string;
    provider: string;
    model: string;
    code_action?: CodeAction | null;
};

export async function sendAIChat(
    request: AIChatRequest,
): Promise<AIChatResponse> {
    try {
        const response = await api.post<AIChatResponse>(
            "/ai/chat",
            request,
            { timeout: 120000 }
        );
        return response.data;
    } catch (error: any) {
        let errorMessage = "AI request failed.";

        if (error?.response?.data?.detail) {
            errorMessage = error.response.data.detail;
        } else if (error?.message) {
            errorMessage = error.message;
        }

        throw new Error(errorMessage);
    }
}