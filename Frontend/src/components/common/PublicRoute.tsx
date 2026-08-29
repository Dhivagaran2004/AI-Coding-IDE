import {
    Navigate,
    Outlet,
} from "react-router-dom";

import {
    useAuth,
} from "../../context/AuthContext";


export default function PublicRoute() {

    const {
        isAuthenticated,
        isLoading,
    } = useAuth();


    if (isLoading) {

        return (
            <div>
                Loading...
            </div>
        );
    }


    if (isAuthenticated) {

        return (
            <Navigate
                to="/projects"
                replace
            />
        );
    }


    return <Outlet />;
}