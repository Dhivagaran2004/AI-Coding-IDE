import api from "./api";
import type { TerminalAIContext } from "./terminalService";
import { getAccessToken } from "../utils/storage";

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
    conversation_id?: number;
};

export type AgentTask = {
    id: string;
    project_id: number;
    task: string;
    mode: "agent";
    status: "pending" | "planning" | "awaiting_approval" | "executing" | "validating" | "paused" | "failed" | "completed" | "cancelled";
    plan: string[];
    actions: {
        action: CodeAction;
        status: "awaiting_approval" | "applied" | "reverted" | "rejected" | "failed";
        error?: string | null;
    }[];
    validation_command?: string | null;
    validation_result?: TerminalAIContext | null;
    steps: {
        sequence: number;
        type: string;
        status: string;
        description: string;
        input?: string;
        output?: string;
        error?: string | null;
    }[];
    stop_reason?: string | null;
    iteration: number;
    changes?: { path: string; content: string; file_id: number }[];
    change_history: { rolled_back?: boolean }[];
};

export type AIChatResponse = {
    message: string;
    provider: string;
    model: string;
    code_action?: CodeAction | null;
    plan?: string[] | null;
    conversation_id?: number | null;
};

export type AIConversationSummary = {
    id: number;
    project_id: number | null;
    user_id: number;
    title: string;
    created_at: string;
    updated_at: string;
};

export type AIConversationDetail = AIConversationSummary & {
    messages: {
        id: number;
        role: "user" | "assistant";
        content: string;
        metadata: Record<string, unknown>;
        created_at: string;
    }[];
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

export async function streamAIChat(
    request: AIChatRequest,
    onToken: (content: string) => void,
    signal: AbortSignal,
): Promise<AIChatResponse> {
    const headers = new Headers({
        "Content-Type": "application/json",
        Accept: "text/event-stream",
    });
    const token = getAccessToken();
    if (token) {
        headers.set("Authorization", "Bearer " + token);
    }

    let response: Response;
    try {
        const baseUrl = (api.defaults.baseURL ?? "").replace(/\/$/, "");
        response = await fetch(baseUrl + "/ai/chat/stream", {
            method: "POST",
            headers,
            body: JSON.stringify(request),
            signal,
        });
    } catch (error) {
        if (signal.aborted) {
            throw error;
        }
        throw new Error("Unable to connect to the AI service.");
    }

    if (!response.ok) {
        let detail = "AI request failed.";
        try {
            const payload = await response.json() as { detail?: string };
            if (payload.detail) {
                detail = payload.detail;
            }
        } catch {
            // Keep the safe generic error when the response is not JSON.
        }
        throw new Error(detail);
    }
    if (!response.body) {
        throw new Error("The AI response stream was unavailable.");
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let pending = "";
    let completedResponse: AIChatResponse | undefined;

    const processEvent = (rawEvent: string) => {
        const data = rawEvent
            .split("\n")
            .filter((line) => line.startsWith("data:"))
            .map((line) => line.slice(5).trimStart())
            .join("\n");
        if (!data) {
            return;
        }
        const event = JSON.parse(data) as
            | { type: "token"; content: string }
            | { type: "done"; response: AIChatResponse }
            | { type: "error"; detail: string };
        if (event.type === "token") {
            onToken(event.content);
        } else if (event.type === "done") {
            completedResponse = event.response;
        } else {
            throw new Error(event.detail);
        }
    };

    try {
        while (true) {
            const { done, value } = await reader.read();
            pending += decoder.decode(value, { stream: !done }).replace(/\r\n/g, "\n");
            const events = pending.split("\n\n");
            pending = events.pop() ?? "";
            for (const event of events) {
                processEvent(event);
            }
            if (done) {
                if (pending.trim()) {
                    processEvent(pending);
                }
                break;
            }
        }
    } catch (error) {
        if (error instanceof SyntaxError) {
            throw new Error("The AI service returned an invalid streaming response.");
        }
        throw error;
    } finally {
        reader.releaseLock();
    }

    if (!completedResponse) {
        throw new Error("The AI response ended before completion.");
    }
    return completedResponse;
}

export async function listAIConversations(
    projectId?: number,
): Promise<AIConversationSummary[]> {
    const response = await api.get<AIConversationSummary[]>(
        "/ai/conversations",
        { params: projectId === undefined ? undefined : { project_id: projectId } },
    );
    return response.data;
}

export async function createAIConversation(
    projectId: number | undefined,
    title: string,
): Promise<AIConversationSummary> {
    const response = await api.post<AIConversationSummary>(
        "/ai/conversations",
        { project_id: projectId, title },
    );
    return response.data;
}

export async function getAIConversation(
    conversationId: number,
): Promise<AIConversationDetail> {
    const response = await api.get<AIConversationDetail>(
        `/ai/conversations/${conversationId}`,
    );
    return response.data;
}

export async function renameAIConversation(
    conversationId: number,
    title: string,
): Promise<AIConversationSummary> {
    const response = await api.patch<AIConversationSummary>(
        `/ai/conversations/${conversationId}`,
        { title },
    );
    return response.data;
}

export async function clearAIConversation(conversationId: number): Promise<void> {
    await api.delete(`/ai/conversations/${conversationId}/messages`);
}

export async function deleteAIConversation(conversationId: number): Promise<void> {
    await api.delete(`/ai/conversations/${conversationId}`);
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

export async function getAgentTasks(projectId?: number): Promise<AgentTask[]> {
    const response = await api.get<AgentTask[]>("/ai/agent/tasks", {
        params: projectId === undefined ? undefined : { project_id: projectId },
    });
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

export async function undoAgentTask(taskId: string): Promise<AgentTask> {
    const response = await api.post<AgentTask>(`/ai/agent/tasks/${taskId}/undo`);
    return response.data;
}