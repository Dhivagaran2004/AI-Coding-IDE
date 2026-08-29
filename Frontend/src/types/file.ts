export interface ProjectFile {
    id: number;
    project_id: number;
    name: string;
    type: "file" | "folder";
    parent_id: number | null;
    path: string | null;
    content: string | null;
    created_at: string;
    updated_at: string;
}


export interface FileTreeNode {
    id: number;
    name: string;
    type: "file" | "folder";
    parent_id: number | null;
    path: string | null;
    children?: FileTreeNode[];
}


export interface ProjectFileCreate {
    name: string;
    type: "file" | "folder";
    parent_id?: number | null;
    content?: string;
}


export interface ProjectFileUpdate {
    name?: string;
    content?: string;
}