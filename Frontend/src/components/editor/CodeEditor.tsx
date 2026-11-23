import Editor from "@monaco-editor/react";

import type { OnMount } from "@monaco-editor/react";

import {
    forwardRef,
    useEffect,
    useImperativeHandle,
    useRef,
} from "react";



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

export type CodeEditorAction = "undo" | "redo" | "selectAll" | "find";

export interface CodeEditorHandle {
    runAction: (action: CodeEditorAction) => void;
}

const CodeEditor = forwardRef<CodeEditorHandle, CodeEditorProps>(function CodeEditor({
    value,
    language,
    onChange,
    onSelectionChange,
}: CodeEditorProps, ref) {

    const editorRef =
        useRef<Parameters<OnMount>[0] | null>(
            null
        );

    const selectionListenerRef =
        useRef<{ dispose: () => void } | null>(null);

    useEffect(() => () => {
        selectionListenerRef.current?.dispose();
    }, []);

    useImperativeHandle(ref, () => ({
        runAction(action) {
            const actionIds: Record<CodeEditorAction, string> = {
                undo: "undo",
                redo: "redo",
                selectAll: "editor.action.selectAll",
                find: "actions.find",
            };
            const editorAction = editorRef.current?.getAction(actionIds[action]);
            void editorAction?.run();
        },
    }), []);

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
});

export default CodeEditor;