export type FileType = "file" | "folder";

export interface ProjectFile {
    id: number;
    project_id: number;
    parent_id: number | null;
    name: string;
    type: FileType;
    content: string | null;
    language: string | null;
    created_at: string;
    updated_at: string;
}

export interface ProjectFileTree {
    id: number;
    name: string;
    type: FileType;
    children: ProjectFileTree[];
}