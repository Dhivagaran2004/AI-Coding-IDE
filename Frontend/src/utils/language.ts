export function getLanguageFromFileName(
    fileName: string
): string {

    const extension =
        fileName
            .split(".")
            .pop()
            ?.toLowerCase();


    const languages: Record<string, string> = {

        js: "javascript",

        jsx: "javascript",

        ts: "typescript",

        tsx: "typescript",

        py: "python",

        java: "java",

        c: "c",

        cpp: "cpp",

        h: "cpp",

        hpp: "cpp",

        cs: "csharp",

        go: "go",

        rs: "rust",

        php: "php",

        rb: "ruby",

        swift: "swift",

        kt: "kotlin",

        html: "html",

        htm: "html",

        css: "css",

        scss: "scss",

        less: "less",

        json: "json",

        xml: "xml",

        yaml: "yaml",

        yml: "yaml",

        md: "markdown",

        sql: "sql",

        sh: "shell",

        bash: "shell",

        dockerfile: "dockerfile",
    };


    if (!extension) {

        return "plaintext";
    }


    return (
        languages[extension] ??
        "plaintext"
    );
}