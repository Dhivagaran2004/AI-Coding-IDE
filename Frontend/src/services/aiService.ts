import api from "./api";

export type AIMessage = {
    role: "system" | "user" | "assistant";
    content: string;
};

export type AIChatRequest = {
    message: string;
    context?: string | null;
    history?: AIMessage[];
};

export type AIChatResponse = {
    message: string;
    provider: string;
    model: string;
};

export async function sendAIChat(
    request: AIChatRequest,
): Promise<AIChatResponse> {
    try {
        const response = await api.post<AIChatResponse>(
            "/ai/chat",
            request,
            { timeout: 30000 }
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