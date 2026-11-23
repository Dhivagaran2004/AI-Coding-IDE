import {
    Blocks,
    Bug,
    CircleUserRound,
    FolderOpen,
    GitBranch,
    Search,
    Settings,
} from "lucide-react";

export type IDEActivityView =
    | "explorer"
    | "search"
    | "source-control"
    | "run-debug"
    | "extensions"
    | "accounts"
    | "settings";

interface IDEActivityBarProps {
    activeView: IDEActivityView | null;
    onSelect: (view: IDEActivityView) => void;
}

const mainViews = [
    { id: "explorer", label: "Explorer", Icon: FolderOpen },
    { id: "search", label: "Search", Icon: Search },
    { id: "source-control", label: "Source Control", Icon: GitBranch },
    { id: "run-debug", label: "Run and Debug", Icon: Bug },
    { id: "extensions", label: "Extensions", Icon: Blocks },
] as const;

const accountViews = [
    { id: "accounts", label: "Accounts", Icon: CircleUserRound },
    { id: "settings", label: "Settings", Icon: Settings },
] as const;

export default function IDEActivityBar({
    activeView,
    onSelect,
}: IDEActivityBarProps) {
    const renderButton = (
        view: IDEActivityView,
        label: string,
        Icon: (typeof mainViews)[number]["Icon"],
    ) => (
        <button
            key={view}
            type="button"
            className={`ide-activity-bar__button${activeView === view ? " is-active" : ""}`}
            title={label}
            aria-label={label}
            aria-pressed={activeView === view}
            onClick={() => onSelect(view)}
        >
            <Icon size={21} strokeWidth={1.7} aria-hidden="true" />
        </button>
    );

    return (
        <nav className="ide-activity-bar" aria-label="IDE views">
            <div className="ide-activity-bar__group">
                {mainViews.map(({ id, label, Icon }) =>
                    renderButton(id, label, Icon),
                )}
            </div>
            <div className="ide-activity-bar__group ide-activity-bar__group--bottom">
                {accountViews.map(({ id, label, Icon }) =>
                    renderButton(id, label, Icon),
                )}
            </div>
        </nav>
    );
}
