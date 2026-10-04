import api from "./api";

export type GitStatus = {
    branch: string;
    changed_files: {
        path: string;
        status: string;
        staged: boolean;
        unstaged: boolean;
    }[];
    staged_files: string[];
    untracked_files: string[];
};

export type GitDiff = {
    diff: string;
    truncated: boolean;
};

export async function getGitStatus(projectId: number): Promise<GitStatus> {
    const response = await api.get<GitStatus>(
        `/projects/${projectId}/git/status`,
    );
    return response.data;
}

export async function getGitDiff(
    projectId: number,
    staged = false,
): Promise<GitDiff> {
    const response = await api.get<GitDiff>(
        `/projects/${projectId}/git/diff`,
        { params: { staged } },
    );
    return response.data;
}
