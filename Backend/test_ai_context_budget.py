import pytest

from App.service.AI.context.context_budget import (
    ContextBudgetExceeded,
    SYSTEM_PROMPT_RESERVE,
    build_budgeted_context,
)


def test_budget_keeps_selected_code_and_recent_messages_before_repository():
    context, history = build_budgeted_context(
        message="Fix the error",
        history=[
            {"role": "user", "content": "older context " * 1000},
            {"role": "assistant", "content": "recent answer"},
        ],
        context_parts=[
            ("repository", "unrelated " * 5000),
            ("terminal", "STDERR: NameError: useful failure"),
            ("selected", "SELECTED CODE CONTEXT\nreturn value"),
            ("current_file", "current file"),
        ],
        max_prompt_chars=SYSTEM_PROMPT_RESERVE + 1200,
    )

    assert "SELECTED CODE CONTEXT\nreturn value" in context
    assert "NameError: useful failure" in context
    assert history[-1]["content"] == "recent answer"
    assert len(context) < 1200
    assert "unrelated " not in context


def test_budget_rejects_selected_code_that_cannot_fit():
    with pytest.raises(ContextBudgetExceeded, match="Selected code"):
        build_budgeted_context(
            message="request",
            history=[],
            context_parts=[("selected", "x" * 1000)],
            max_prompt_chars=SYSTEM_PROMPT_RESERVE + 100,
        )


def test_budget_accounts_for_message_context_and_history():
    context, history = build_budgeted_context(
        message="m" * 100,
        history=[
            {"role": "user", "content": "a" * 2000},
            {"role": "assistant", "content": "b" * 2000},
        ],
        context_parts=[
            ("selected", "selected"),
            ("terminal", "STDERR: traceback"),
            ("repository", "r" * 5000),
        ],
        max_prompt_chars=SYSTEM_PROMPT_RESERVE + 1300,
    )

    accounted = (
        SYSTEM_PROMPT_RESERVE
        + len("m" * 100)
        + sum(len(item["content"]) + 32 for item in history)
        + len(context)
    )
    assert accounted <= SYSTEM_PROMPT_RESERVE + 1300
    assert history[-1]["role"] == "assistant"
    assert "selected" in context
    assert "STDERR" in context
