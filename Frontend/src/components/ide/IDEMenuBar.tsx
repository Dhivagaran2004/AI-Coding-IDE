import { useEffect, useRef, useState } from "react";

export interface IDEMenuItem {
    label: string;
    shortcut?: string;
    disabled?: boolean;
    onSelect: () => void;
}

export interface IDEMenu {
    label: string;
    items: IDEMenuItem[];
}

interface IDEMenuBarProps {
    menus: IDEMenu[];
}

export default function IDEMenuBar({ menus }: IDEMenuBarProps) {
    const [activeMenu, setActiveMenu] = useState<string | null>(null);
    const menuBarRef = useRef<HTMLElement | null>(null);
    const firstItemRef = useRef<HTMLButtonElement | null>(null);

    useEffect(() => {
        if (activeMenu === null) {
            return;
        }

        const closeOnOutsidePointer = (event: PointerEvent) => {
            if (!menuBarRef.current?.contains(event.target as Node)) {
                setActiveMenu(null);
            }
        };
        const closeOnEscape = (event: KeyboardEvent) => {
            if (event.key === "Escape") {
                setActiveMenu(null);
            }
        };

        document.addEventListener("pointerdown", closeOnOutsidePointer);
        document.addEventListener("keydown", closeOnEscape);
        requestAnimationFrame(() => firstItemRef.current?.focus());
        return () => {
            document.removeEventListener("pointerdown", closeOnOutsidePointer);
            document.removeEventListener("keydown", closeOnEscape);
        };
    }, [activeMenu]);

    return (
        <nav className="ide-menubar" aria-label="IDE menu" role="menubar" ref={menuBarRef}>
            {menus.map((menu) => (
                <div className="ide-menubar__group" key={menu.label}>
                    <button
                        type="button"
                        className={`ide-menubar__trigger${activeMenu === menu.label ? " is-open" : ""}`}
                        role="menuitem"
                        aria-haspopup="menu"
                        aria-expanded={activeMenu === menu.label}
                        onClick={() =>
                            setActiveMenu((current) =>
                                current === menu.label ? null : menu.label,
                            )
                        }
                    >
                        {menu.label}
                    </button>
                    {activeMenu === menu.label && (
                        <div className="ide-menubar__dropdown" role="menu" aria-label={`${menu.label} menu`}>
                            {menu.items.map((item, index) => (
                                <button
                                    key={`${menu.label}-${item.label}`}
                                    ref={index === 0 ? firstItemRef : undefined}
                                    type="button"
                                    role="menuitem"
                                    className="ide-menubar__item"
                                    disabled={item.disabled}
                                    onClick={() => {
                                        setActiveMenu(null);
                                        item.onSelect();
                                    }}
                                >
                                    <span>{item.label}</span>
                                    {item.shortcut && (
                                        <kbd>{item.shortcut}</kbd>
                                    )}
                                </button>
                            ))}
                        </div>
                    )}
                </div>
            ))}
        </nav>
    );
}
