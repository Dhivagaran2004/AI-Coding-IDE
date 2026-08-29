import {
    type FormEvent,
    useState,
} from "react";

import {
    useNavigate,
    Link,
} from "react-router-dom";

import { useAuth } from "../context/AuthContext";


export default function LoginPage() {

    const navigate = useNavigate();

    const { login } = useAuth();

    const [email, setEmail] = useState("");

    const [password, setPassword] = useState("");

    const [error, setError] = useState("");

    const [isSubmitting, setIsSubmitting] = useState(false);


    async function handleSubmit(
        event: FormEvent<HTMLFormElement>
    ) {

        event.preventDefault();

        setError("");

        setIsSubmitting(true);


        try {

            await login({
                email,
                password,
            });

            navigate("/projects");

        } catch (error: any) {

            if (
                error.response?.status === 401
            ) {

                setError(
                    "Invalid email or password."
                );

            } else {

                setError(
                    "Unable to login. Please try again."
                );
            }

        } finally {

            setIsSubmitting(false);

        }
    }


    return (
        <div className="auth-page">

            <div className="auth-card">

                <h1>AI Coding IDE</h1>

                <h2>Login</h2>

                {error && (
                    <div className="auth-error">
                        {error}
                    </div>
                )}


                <form onSubmit={handleSubmit}>

                    <div className="form-group">

                        <label htmlFor="email">
                            Email
                        </label>

                        <input
                            id="email"
                            type="email"
                            value={email}
                            onChange={(event) =>
                                setEmail(event.target.value)
                            }
                            required
                        />

                    </div>


                    <div className="form-group">

                        <label htmlFor="password">
                            Password
                        </label>

                        <input
                            id="password"
                            type="password"
                            value={password}
                            onChange={(event) =>
                                setPassword(event.target.value)
                            }
                            required
                        />

                    </div>


                    <button
                        type="submit"
                        disabled={isSubmitting}
                    >
                        {isSubmitting
                            ? "Logging in..."
                            : "Login"}
                    </button>

                </form>


                <p>
                    Don't have an account?{" "}
                    <Link to="/register">
                        Register
                    </Link>
                </p>

            </div>

        </div>
    );
}