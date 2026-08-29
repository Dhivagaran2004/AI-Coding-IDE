import Editor from "@monaco-editor/react";

import type { OnMount } from "@monaco-editor/react";

import { useRef } from "react";


interface CodeEditorProps {

    value: string;

    language: string;

    onChange?: (
        value: string | undefined
    ) => void;
}


export default function CodeEditor({
    value,
    language,
    onChange,
}: CodeEditorProps) {

    const editorRef =
        useRef<Parameters<OnMount>[0] | null>(
            null
        );


    function handleEditorMount(
        editor: Parameters<OnMount>[0]
    ) {

        editorRef.current = editor;

        editor.focus();
    }


    return (
        <div className="code-editor-container">

            <Editor
                height="100%"
                width="100%"

                theme="vs-dark"

                language={language}

                value={value}

                onMount={
                    handleEditorMount
                }

                onChange={onChange}

                options={{
                    automaticLayout: true,

                    minimap: {
                        enabled: true,
                    },

                    fontSize: 14,

                    lineNumbers: "on",

                    wordWrap: "on",

                    tabSize: 4,

                    scrollBeyondLastLine: false,

                    smoothScrolling: true,

                    cursorBlinking: "smooth",

                    padding: {
                        top: 12,
                        bottom: 12,
                    },

                    suggestOnTriggerCharacters: true,

                    quickSuggestions: true,
                }}
            />

        </div>
    );
}