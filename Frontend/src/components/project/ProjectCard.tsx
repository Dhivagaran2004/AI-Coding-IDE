import type { Project } from "../../types/project";

import {
    Folder,
    Trash2,
    ExternalLink,
} from "lucide-react";


interface ProjectCardProps {
    project: Project;
    onOpen: (project: Project) => void;
    onDelete: (project: Project) => void;
}


export default function ProjectCard({
    project,
    onOpen,
    onDelete,
}: ProjectCardProps) {

    return (
        <div className="project-card">

            <div className="project-card-header">

                <div className="project-icon">
                    <Folder size={24} />
                </div>

                <div className="project-card-actions">

                    <button
                        type="button"
                        title="Open project"
                        onClick={() => onOpen(project)}
                    >
                        <ExternalLink size={18} />
                    </button>

                    <button
                        type="button"
                        title="Delete project"
                        onClick={() => onDelete(project)}
                    >
                        <Trash2 size={18} />
                    </button>

                </div>

            </div>


            <h3>
                {project.name}
            </h3>


            {project.description && (
                <p className="project-description">
                    {project.description}
                </p>
            )}


            {project.language && (
                <span className="project-language">
                    {project.language}
                </span>
            )}


            <button
                type="button"
                className="open-project-button"
                onClick={() => onOpen(project)}
            >
                Open Project
            </button>

        </div>
    );
}