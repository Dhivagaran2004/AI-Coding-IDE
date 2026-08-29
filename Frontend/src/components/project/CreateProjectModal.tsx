import {
    useState,
} from "react";

import type {
    FormEvent,
} from "react";

import {
    X,
} from "lucide-react";

import type {
    ProjectCreate,
} from "../../types/project";


interface CreateProjectModalProps {
    isOpen: boolean;
    isCreating: boolean;
    onClose: () => void;
    onCreate: (
        data: ProjectCreate
    ) => Promise<void>;
}


export default function CreateProjectModal({
    isOpen,
    isCreating,
    onClose,
    onCreate,
}: CreateProjectModalProps) {

    const [name, setName] = useState("");

    const [description, setDescription] =
        useState("");

    const [language, setLanguage] =
        useState("");


    if (!isOpen) {
        return null;
    }


    async function handleSubmit(
        event: FormEvent<HTMLFormElement>
    ) {

        event.preventDefault();


        if (!name.trim()) {
            return;
        }


        await onCreate({
            name: name.trim(),
            description:
                description.trim() || undefined,
            language:
                language.trim() || undefined,
        });


        setName("");
        setDescription("");
        setLanguage("");
    }


    return (
        <div className="modal-overlay">

            <div className="modal">

                <div className="modal-header">

                    <h2>
                        Create New Project
                    </h2>

                    <button
                        type="button"
                        onClick={onClose}
                        disabled={isCreating}
                    >
                        <X size={20} />
                    </button>

                </div>


                <form onSubmit={handleSubmit}>

                    <div className="form-group">

                        <label htmlFor="project-name">
                            Project Name
                        </label>

                        <input
                            id="project-name"
                            type="text"
                            value={name}
                            onChange={(event) =>
                                setName(event.target.value)
                            }
                            placeholder="My AI Project"
                            required
                            maxLength={255}
                        />

                    </div>


                    <div className="form-group">

                        <label htmlFor="project-description">
                            Description
                        </label>

                        <textarea
                            id="project-description"
                            value={description}
                            onChange={(event) =>
                                setDescription(
                                    event.target.value
                                )
                            }
                            placeholder="Describe your project"
                            rows={4}
                        />

                    </div>


                    <div className="form-group">

                        <label htmlFor="project-language">
                            Language
                        </label>

                        <input
                            id="project-language"
                            type="text"
                            value={language}
                            onChange={(event) =>
                                setLanguage(
                                    event.target.value
                                )
                            }
                            placeholder="Python"
                        />

                    </div>


                    <div className="modal-footer">

                        <button
                            type="button"
                            onClick={onClose}
                            disabled={isCreating}
                        >
                            Cancel
                        </button>


                        <button
                            type="submit"
                            disabled={
                                isCreating ||
                                !name.trim()
                            }
                        >
                            {isCreating
                                ? "Creating..."
                                : "Create Project"}
                        </button>

                    </div>

                </form>

            </div>

        </div>
    );
}