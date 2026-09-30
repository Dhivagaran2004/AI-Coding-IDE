import Editor from "@monaco-editor/react";

import type { OnMount } from "@monaco-editor/react";

import { useEffect, useRef } from "react";



interface CodeEditorProps {

    value: string;

    language: string;

    onChange?: (
        value: string | undefined
    ) => void;

    onSelectionChange?: (
        selection: {
            text: string;
            startLine: number;
            endLine: number;
        } | null
    ) => void;
}


export default function CodeEditor({
    value,
    language,
    onChange,
    onSelectionChange,
}: CodeEditorProps) {

    const editorRef =
        useRef<Parameters<OnMount>[0] | null>(
            null
        );

    const selectionListenerRef =
        useRef<{ dispose: () => void } | null>(null);

    useEffect(() => () => {
        selectionListenerRef.current?.dispose();
    }, []);


    function handleEditorMount(
        editor: Parameters<OnMount>[0]
    ) {

        editorRef.current = editor;

        const publishSelection = () => {
            const selection = editor.getSelection();
            const model = editor.getModel();

            if (!selection || selection.isEmpty() || !model) {
                onSelectionChange?.(null);
                return;
            }

            onSelectionChange?.({
                text: model.getValueInRange(selection),
                startLine: selection.startLineNumber,
                endLine: selection.endLineNumber,
            });
        };

        selectionListenerRef.current =
            editor.onDidChangeCursorSelection(publishSelection);
        publishSelection();

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