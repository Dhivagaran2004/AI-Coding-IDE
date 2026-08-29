import {
    BrowserRouter,
    Routes,
    Route,
    Navigate,
} from "react-router-dom";


import {
    AuthProvider,
} from "./context/AuthContext";


import LoginPage from "./pages/LoginPage";

import RegisterPage from "./pages/RegisterPage";

import ProjectsPage from "./pages/ProjectsPage";

import IDEPage from "./pages/IDEPage";


import ProtectedRoute from
    "./components/common/ProtectedRoute";

import PublicRoute from
    "./components/common/PublicRoute";


function App() {

    return (
        <BrowserRouter>

            <AuthProvider>

                <Routes>

                    <Route
                        element={
                            <PublicRoute />
                        }
                    >

                        <Route
                            path="/login"
                            element={
                                <LoginPage />
                            }
                        />

                        <Route
                            path="/register"
                            element={
                                <RegisterPage />
                            }
                        />

                    </Route>


                    <Route
                        element={
                            <ProtectedRoute />
                        }
                    >

                        <Route
                            path="/projects"
                            element={
                                <ProjectsPage />
                            }
                        />


                        <Route
                            path="/projects/:projectId/ide"
                            element={
                                <IDEPage />
                            }
                        />

                    </Route>


                    <Route
                        path="/"
                        element={
                            <Navigate
                                to="/projects"
                                replace
                            />
                        }
                    />


                    <Route
                        path="*"
                        element={
                            <Navigate
                                to="/projects"
                                replace
                            />
                        }
                    />

                </Routes>

            </AuthProvider>

        </BrowserRouter>
    );
}


export default App;