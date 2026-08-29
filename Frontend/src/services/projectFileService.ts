import api from "./api";

import type {
    ProjectFile,
    ProjectFileTree,
} from "../types/projectFile";


export async function getProjectFiles(
    projectId: number
): Promise<ProjectFile[]> {

    const response = await api.get(
        `/projects/${projectId}/files`
    );

    return response.data;
}


export async function getProjectFileTree(
    projectId: number
): Promise<ProjectFileTree[]> {

    const response = await api.get(
        `/projects/${projectId}/files/tree/all`
    );

    return response.data;
}


export async function getProjectFile(
    projectId: number,
    fileId: number
): Promise<ProjectFile> {

    const response = await api.get(
        `/projects/${projectId}/files/${fileId}`
    );

    return response.data;
}