import api from "./api";

import type {
    FileTreeNode,
    ProjectFile,
    ProjectFileCreate,
    ProjectFileUpdate,
} from "../types/file";


export async function getProjectFileTree(
    projectId: number
): Promise<FileTreeNode[]> {

    const response =
        await api.get<FileTreeNode[]>(
            `/projects/${projectId}/files/tree/all`
        );

    return response.data;
}


export async function getProjectFiles(
    projectId: number
): Promise<ProjectFile[]> {

    const response =
        await api.get<ProjectFile[]>(
            `/projects/${projectId}/files`
        );

    return response.data;
}


export async function createProjectFile(
    projectId: number,
    data: ProjectFileCreate
): Promise<ProjectFile> {

    const response =
        await api.post<ProjectFile>(
            `/projects/${projectId}/files`,
            data
        );

    return response.data;
}


export async function updateProjectFile(
    projectId: number,
    fileId: number,
    data: ProjectFileUpdate
): Promise<ProjectFile> {

    const response =
        await api.put<ProjectFile>(
            `/projects/${projectId}/files/${fileId}`,
            data
        );

    return response.data;
}


export async function deleteProjectFile(
    projectId: number,
    fileId: number
): Promise<void> {

    await api.delete(
        `/projects/${projectId}/files/${fileId}`
    );
}