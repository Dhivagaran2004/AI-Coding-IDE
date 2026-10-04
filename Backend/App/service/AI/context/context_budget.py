from collections.abc import Sequence


SYSTEM_PROMPT_RESERVE = 2048
MAX_HISTORY_CHARS = 20000


class ContextBudgetExceeded(ValueError):
    pass


def _history_size(history: Sequence[dict[str, str]]) -> int:
    return sum(len(item.get("content", "")) for item in history)


def _bounded_recent_history(
    history: Sequence[dict[str, str]],
    max_chars: int,
) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    used = 0
    for item in reversed(history):
        role = item.get("role", "user")
        content = item.get("content", "")
        remaining = max_chars - used
        if remaining <= 0:
            break
        if len(content) > remaining:
            result.append({"role": role, "content": content[-remaining:]})
            break
        result.append({"role": role, "content": content})
        used += len(content)
    return list(reversed(result))


def build_budgeted_context(
    message: str,
    history: Sequence[dict[str, str]],
    context_parts: Sequence[tuple[str, str | None]],
    max_prompt_chars: int,
) -> tuple[str | None, list[dict[str, str]]]:
    """Bound context, history, message, and a reserved system-prompt allowance."""
    if max_prompt_chars <= 0:
        raise ValueError("max_prompt_chars must be greater than zero.")

    available = max_prompt_chars - SYSTEM_PROMPT_RESERVE - len(message)
    if available < 0:
        raise ContextBudgetExceeded(
            "The message exceeds the configured AI prompt budget."
        )

    history_limit = min(MAX_HISTORY_CHARS, available // 4, _history_size(history))
    bounded_history = _bounded_recent_history(history, history_limit)
    available -= _history_size(bounded_history)
    available -= len(bounded_history) * 32

    selected = next(
        (value for name, value in context_parts if name == "selected" and value),
        None,
    )
    if selected and len(selected) + len("SELECTED CODE CONTEXT\n\n") > available:
        raise ContextBudgetExceeded(
            "Selected code exceeds the configured AI prompt budget."
        )

    labels = {
        "selected": "SELECTED CODE CONTEXT",
        "terminal": "TERMINAL CONTEXT",
        "current_file": "CURRENT FILE CONTEXT",
        "repository": "REPOSITORY CONTEXT",
    }
    header_reserve = sum(
        len(labels.get(name, name.replace("_", " ").upper())) + 4
        for name, value in context_parts
        if value
    )
    header_reserve += max(0, len([value for _, value in context_parts if value]) - 1) * 2
    available -= header_reserve
    if available < 0:
        raise ContextBudgetExceeded(
            "The supplied context exceeds the configured AI prompt budget."
        )
    priorities = {
        "selected": 0,
        "terminal": 1,
        "current_file": 2,
        "repository": 3,
    }
    kept: dict[str, str] = {}
    used = 0
    prioritized_parts = sorted(
        enumerate(context_parts),
        key=lambda item: (priorities.get(item[1][0], 4), item[0]),
    )
    for _, (name, value) in prioritized_parts:
        if not value:
            continue
        label = labels.get(name, name.replace("_", " ").upper())
        block = f"{label}\n\n{value}"
        remaining = available - used
        if remaining <= 0:
            continue
        if len(block) > remaining:
            if name == "selected":
                raise ContextBudgetExceeded(
                    "Selected code exceeds the configured AI prompt budget."
                )
            if name == "repository":
                continue
            kept[name] = block[:remaining]
            used += remaining
            continue
        kept[name] = block
        used += len(block)

    blocks = [
        kept[name]
        for name, value in context_parts
        if value and name in kept
    ]
    return ("\n\n".join(blocks) or None), bounded_history
