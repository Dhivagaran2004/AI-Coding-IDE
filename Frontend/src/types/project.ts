export interface Project {
    id: number;
    user_id: number;
    name: string;
    description: string | null;
    language: string | null;
    created_at: string;
    updated_at: string;
}


export interface ProjectCreate {
    name: string;
    description?: string;
    language?: string;
}