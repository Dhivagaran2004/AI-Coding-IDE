import api from "./api";

import type {
    LoginRequest,
    RegisterRequest,
    TokenResponse,
    User,
} from "../types/auth";

import {
    setAccessToken,
} from "../utils/storage";


export async function login(
    credentials: LoginRequest
): Promise<TokenResponse> {

    const formData = new URLSearchParams();

    formData.append(
        "username",
        credentials.email
    );

    formData.append(
        "password",
        credentials.password
    );

    const response = await api.post<TokenResponse>(
        "/auth/login",
        formData,
        {
            headers: {
                "Content-Type":
                    "application/x-www-form-urlencoded",
            },
        }
    );

    const tokenData = response.data;

    setAccessToken(
        tokenData.access_token
    );

    return tokenData;
}


export async function register(
    data: RegisterRequest
): Promise<User> {

    const response = await api.post<User>(
        "/auth/register",
        data
    );

    return response.data;
}