import {
    useEffect,
    useState,
} from "react";

import {
    useNavigate,
} from "react-router-dom";

import {
    Plus,
    LogOut,
    RefreshCw,
} from "lucide-react";

import {
    useAuth,
} from "../context/AuthContext";

import {
    getProjects,
    createProject,
    deleteProject,
} from "../services/projectService";

import type {
    Project,
    ProjectCreate,
} from "../types/project";

import ProjectCard from "../components/project/ProjectCard";

import CreateProjectModal from
    "../components/project/CreateProjectModal";


export default function ProjectsPage() {

    const navigate = useNavigate();

    const {
        logout,
    } = useAuth();


    const [projects, setProjects] =
        useState<Project[]>([]);


    const [isLoading, setIsLoading] =
        useState(true);


    const [error, setError] =
        useState("");


    const [isModalOpen, setIsModalOpen] =
        useState(false);


    const [isCreating, setIsCreating] =
        useState(false);


    async function loadProjects() {

        try {

            setError("");

            setIsLoading(true);

            const data =
                await getProjects();

            setProjects(data);

        } catch (error: any) {

            if (
                error.response?.status === 401
            ) {

                logout();

                navigate("/login");

                return;
            }


            setError(
                "Unable to load projects."
            );

        } finally {

            setIsLoading(false);

        }
    }


    useEffect(() => {

        loadProjects();

    }, []);


    async function handleCreateProject(
        data: ProjectCreate
    ) {

        try {

            setError("");

            setIsCreating(true);

            const project =
                await createProject(data);


            setProjects((currentProjects) => [
                project,
                ...currentProjects,
            ]);


            setIsModalOpen(false);

        } catch (error: any) {

            if (
                error.response?.status === 401
            ) {

                logout();

                navigate("/login");

                return;
            }


            setError(
                error.response?.data?.detail ??
                "Unable to create project."
            );

        } finally {

            setIsCreating(false);

        }
    }


    async function handleDeleteProject(
        project: Project
    ) {

        const confirmed =
            window.confirm(
                `Delete "${project.name}"?`
            );


        if (!confirmed) {
            return;
        }


        try {

            setError("");

            await deleteProject(
                project.id
            );


            setProjects(
                (currentProjects) =>
                    currentProjects.filter(
                        (item) =>
                            item.id !== project.id
                    )
            );

        } catch (error: any) {

            if (
                error.response?.status === 401
            ) {

                logout();

                navigate("/login");

                return;
            }


            setError(
                error.response?.data?.detail ??
                "Unable to delete project."
            );

        }
    }


    function handleOpenProject(
        project: Project
    ) {

        navigate(
            `/projects/${project.id}/ide`
        );
    }


    function handleLogout() {

        logout();

        navigate("/login");
    }


    return (
        <div className="projects-page">

            <header className="dashboard-header">

                <div>

                    <h1>
                        AI Coding IDE
                    </h1>

                    <span>
                        Project Dashboard
                    </span>

                </div>


                <button
                    type="button"
                    onClick={handleLogout}
                    className="logout-button"
                >
                    <LogOut size={18} />

                    Logout
                </button>

            </header>


            <main className="projects-content">

                <div className="projects-toolbar">

                    <div>

                        <h2>
                            My Projects
                        </h2>

                        <p>
                            Create and manage
                            your coding projects.
                        </p>

                    </div>


                    <div className="toolbar-actions">

                        <button
                            type="button"
                            onClick={loadProjects}
                            disabled={isLoading}
                        >
                            <RefreshCw size={18} />

                            Refresh
                        </button>


                        <button
                            type="button"
                            className="create-project-button"
                            onClick={() =>
                                setIsModalOpen(true)
                            }
                        >
                            <Plus size={18} />

                            New Project
                        </button>

                    </div>

                </div>


                {error && (
                    <div className="dashboard-error">
                        {error}
                    </div>
                )}


                {isLoading ? (

                    <div className="projects-loading">
                        Loading projects...
                    </div>

                ) : projects.length === 0 ? (

                    <div className="empty-projects">

                        <h3>
                            No projects yet
                        </h3>

                        <p>
                            Create your first
                            coding project.
                        </p>

                        <button
                            type="button"
                            onClick={() =>
                                setIsModalOpen(true)
                            }
                        >
                            <Plus size={18} />

                            Create Project
                        </button>

                    </div>

                ) : (

                    <div className="projects-grid">

                        {projects.map(
                            (project) => (

                                <ProjectCard
                                    key={project.id}
                                    project={project}
                                    onOpen={
                                        handleOpenProject
                                    }
                                    onDelete={
                                        handleDeleteProject
                                    }
                                />

                            )
                        )}

                    </div>

                )}

            </main>


            <CreateProjectModal
                isOpen={isModalOpen}
                isCreating={isCreating}
                onClose={() =>
                    setIsModalOpen(false)
                }
                onCreate={
                    handleCreateProject
                }
            />

        </div>
    );
}