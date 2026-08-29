import {
    createContext,
    useContext,
    useEffect,
    useState,
    type ReactNode,
} from "react";

import {
    getAccessToken,
    removeAccessToken,
    setAccessToken,
} from "../utils/storage";

import { login as loginRequest } from "../services/authService";

import type {
    LoginRequest,
    User,
} from "../types/auth";


interface AuthContextType {
    user: User | null;
    token: string | null;
    isAuthenticated: boolean;
    isLoading: boolean;
    login: (credentials: LoginRequest) => Promise<void>;
    logout: () => void;
}


const AuthContext = createContext<
    AuthContextType | undefined
>(undefined);


interface AuthProviderProps {
    children: ReactNode;
}


export function AuthProvider({
    children,
}: AuthProviderProps) {

    const [token, setToken] = useState<string | null>(
        getAccessToken()
    );

    const [user, setUser] = useState<User | null>(null);

    const [isLoading, setIsLoading] = useState(true);


    useEffect(() => {

        const storedToken = getAccessToken();

        if (storedToken) {
            setToken(storedToken);
        }

        setIsLoading(false);

    }, []);


    async function login(
        credentials: LoginRequest
    ): Promise<void> {

        const tokenData = await loginRequest(
            credentials
        );

        setAccessToken(
            tokenData.access_token
        );

        setToken(
            tokenData.access_token
        );

        /*
         * User information can be loaded
         * from a /auth/me endpoint later.
         */
    }


    function logout(): void {

        removeAccessToken();

        setToken(null);

        setUser(null);
    }


    const value: AuthContextType = {
        user,
        token,
        isAuthenticated: Boolean(token),
        isLoading,
        login,
        logout,
    };


    return (
        <AuthContext.Provider value={value}>
            {children}
        </AuthContext.Provider>
    );
}


export function useAuth(): AuthContextType {

    const context = useContext(AuthContext);

    if (!context) {
        throw new Error(
            "useAuth must be used inside AuthProvider"
        );
    }

    return context;
}