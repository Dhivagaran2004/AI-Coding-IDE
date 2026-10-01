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
    mode?: "ask" | "plan" | "edit";
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

export type AgentTask = {
    id: string;
    project_id: number;
    task: string;
    mode: "agent";
    status: "pending" | "planning" | "awaiting_approval" | "executing" | "validating" | "failed" | "completed" | "cancelled";
    plan: string[];
    actions: {
        action: CodeAction;
        status: "awaiting_approval" | "applied" | "rejected" | "failed";
        error?: string | null;
    }[];
    validation_command?: string | null;
    validation_result?: TerminalAIContext | null;
    steps: {
        sequence: number;
        type: string;
        status: string;
        description: string;
        output?: string;
        error?: string | null;
    }[];
    stop_reason?: string | null;
    iteration: number;
    changes?: { path: string; content: string; file_id: number }[];
};

export type AIChatResponse = {
    message: string;
    provider: string;
    model: string;
    code_action?: CodeAction | null;
    plan?: string[] | null;
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

export async function createAgentTask(
    projectId: number,
    task: string,
    context?: {
        context?: string | null;
        current_file_path?: string | null;
        selected_code?: AIChatRequest["selected_code"];
        terminal_context?: TerminalAIContext | null;
    },
): Promise<AgentTask> {
    const response = await api.post<AgentTask>("/ai/agent/tasks", {
        project_id: projectId,
        task,
        ...context,
    });
    return response.data;
}

export async function getAgentTask(taskId: string): Promise<AgentTask> {
    const response = await api.get<AgentTask>(`/ai/agent/tasks/${taskId}`);
    return response.data;
}

export async function approveAgentChanges(
    taskId: string,
    approval: { action_indexes?: number[]; reject_indexes?: number[]; accept_all?: boolean; reject_all?: boolean },
): Promise<AgentTask> {
    const response = await api.post<AgentTask>(
        `/ai/agent/tasks/${taskId}/approve`,
        approval,
    );
    return response.data;
}

export async function approveAgentValidation(
    taskId: string,
    command: string,
    approved: boolean,
): Promise<AgentTask> {
    const response = await api.post<AgentTask>(
        `/ai/agent/tasks/${taskId}/validate`,
        { command, approved },
    );
    return response.data;
}

export async function continueAgentTask(taskId: string): Promise<AgentTask> {
    const response = await api.post<AgentTask>(`/ai/agent/tasks/${taskId}/continue`);
    return response.data;
}

export async function cancelAgentTask(taskId: string): Promise<AgentTask> {
    const response = await api.post<AgentTask>(`/ai/agent/tasks/${taskId}/cancel`);
    return response.data;
}