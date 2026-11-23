export interface LoginRequest {
    email: string;
    password: string;
}

export interface RegisterRequest {
    name?: string;
    username?: string;
    email: string;
    password: string;
}

export interface TokenResponse {
    access_token: string;
    token_type: string;
}

export interface User {
    id: number;
    email: string;
}