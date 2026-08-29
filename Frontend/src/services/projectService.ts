import api from "./api";

import type {
    Project,
    ProjectCreate,
} from "../types/project";


export async function getProjects(): Promise<Project[]> {

    const response = await api.get<Project[]>(
        "/projects/"
    );

    return response.data;
}


export async function createProject(
    data: ProjectCreate
): Promise<Project> {

    const response = await api.post<Project>(
        "/projects/",
        data
    );

    return response.data;
}


export async function deleteProject(
    projectId: number
): Promise<void> {

    await api.delete(
        `/projects/${projectId}`
    );
}