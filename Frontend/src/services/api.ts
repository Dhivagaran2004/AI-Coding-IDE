import axios from "axios";
import { getAccessToken, removeAccessToken } from "../utils/storage";

const api = axios.create({
    baseURL:
        import.meta.env.VITE_API_URL ||
        "http://127.0.0.1:8000",
    headers: {
        "Content-Type": "application/json",
    },
});

let isRedirectingToLogin = false;

api.interceptors.request.use(
    (config) => {
        const token = getAccessToken();

        if (token) {
            config.headers.Authorization = `Bearer ${token}`;
        }

        return config;
    },
    (error) => {
        return Promise.reject(error);
    }
);

api.interceptors.response.use(
    (response) => response,
    (error: unknown) => {
        if (
            axios.isAxiosError(error) &&
            error.response?.status === 401 &&
            !error.config?.url?.includes("/auth/login")
        ) {
            removeAccessToken();
            if (
                !isRedirectingToLogin &&
                window.location.pathname !== "/login"
            ) {
                isRedirectingToLogin = true;
                window.location.replace("/login");
            }
        }
        return Promise.reject(error);
    },
);

export default api;