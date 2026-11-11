import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from App.service.AI.providers import provider_factory
from App.service.AI.providers.groq_provider import GroqProvider
from App.service.AI.agent.agent_task_service import TaskPlanner


def test_factory_builds_groq_provider_from_groq_settings(monkeypatch):
    monkeypatch.setattr(provider_factory, "GROQ_API_KEY", "test-groq-key")
    monkeypatch.setattr(provider_factory, "GROQ_MODEL", "test-model")

    provider = provider_factory.get_llm_provider(" Groq ")

    assert isinstance(provider, GroqProvider)
    assert provider.model == "test-model"
    assert str(provider.client.base_url).rstrip("/") == GroqProvider.BASE_URL
    assert provider.client.timeout == GroqProvider.REQUEST_TIMEOUT_SECONDS
    assert provider.client.max_retries == 0


def test_factory_requires_groq_api_key_without_exposing_it(monkeypatch):
    monkeypatch.setattr(provider_factory, "GROQ_API_KEY", "")

    with pytest.raises(ValueError, match="GROQ_API_KEY is required"):
        provider_factory.get_llm_provider("groq")


def test_groq_provider_generate_passes_history_and_context_to_sdk():
    provider = GroqProvider(model="test-model", api_key="test-groq-key")
    provider.client.chat.completions.create = AsyncMock(
        return_value=SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content="Generated answer"),
                )
            ]
        )
    )
    history = [{"role": "user", "content": "Earlier question"}]

    response = asyncio.run(
        provider.generate(
            "Current question",
            context="project context",
            history=history,
        )
    )

    assert response == "Generated answer"
    call = provider.client.chat.completions.create.await_args
    assert call.kwargs["model"] == "test-model"
    assert call.kwargs["messages"][1] == history[0]
    assert "project context" in call.kwargs["messages"][-1]["content"]
    assert "Current question" in call.kwargs["messages"][-1]["content"]
    assert "test-groq-key" not in repr(call.kwargs)


def test_groq_text_response_remains_compatible_with_agent_plan_parser():
    provider = GroqProvider(model="test-model", api_key="test-groq-key")
    plan_json = (
        '{"plan":["Update the function"],"actions":[],"validation_command":null}'
    )
    provider.client.chat.completions.create = AsyncMock(
        return_value=SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=plan_json),
                )
            ]
        )
    )

    response = asyncio.run(provider.generate("Plan this change"))

    assert TaskPlanner.parse(response) == (["Update the function"], [], None)


def test_groq_json_generation_enables_json_object_mode():
    provider = GroqProvider(model="test-model", api_key="test-groq-key")
    plan_json = (
        '{"plan":["Update the function"],"actions":[],"validation_command":null}'
    )
    provider.client.chat.completions.create = AsyncMock(
        return_value=SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=plan_json),
                )
            ]
        )
    )

    response = asyncio.run(
        provider.generate_json(
            "Return a JSON plan",
            context="Project context",
        )
    )

    assert TaskPlanner.parse(response) == (["Update the function"], [], None)
    response_format = provider.client.chat.completions.create.await_args.kwargs[
        "response_format"
    ]
    assert response_format == {"type": "json_object"}
    system_message = (
        provider.client.chat.completions.create.await_args.kwargs["messages"][0][
            "content"
        ]
    )
    assert "no Markdown, code fences, or surrounding prose" in system_message
    assert "fenced JSON code actions" not in system_message


def test_groq_json_validation_error_falls_back_to_prompted_json():
    provider = GroqProvider(model="test-model", api_key="test-groq-key")
    plan_json = (
        '{"plan":["Update the function"],"actions":[],"validation_command":null}'
    )
    provider.client.chat.completions.create = AsyncMock(
        side_effect=[
            type(
                "JsonValidationError",
                (RuntimeError,),
                {"code": "json_validate_failed"},
            )("JSON validation failed"),
            SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content=plan_json),
                    )
                ]
            ),
        ]
    )

    response = asyncio.run(provider.generate_json("Return a JSON plan"))

    assert TaskPlanner.parse(response) == (["Update the function"], [], None)
    assert provider.client.chat.completions.create.await_count == 2
    assert (
        provider.client.chat.completions.create.await_args_list[0].kwargs[
            "response_format"
        ]
        == {"type": "json_object"}
    )
    assert "response_format" not in (
        provider.client.chat.completions.create.await_args_list[1].kwargs
    )


def test_groq_json_generation_does_not_fallback_for_other_errors():
    provider = GroqProvider(model="test-model", api_key="test-groq-key")
    provider.client.chat.completions.create = AsyncMock(
        side_effect=RuntimeError("provider unavailable")
    )

    with pytest.raises(RuntimeError, match="provider unavailable"):
        asyncio.run(provider.generate_json("Return a JSON plan"))

    assert provider.client.chat.completions.create.await_count == 1


def test_groq_provider_stream_yields_only_text_chunks():
    provider = GroqProvider(model="test-model", api_key="test-groq-key")

    async def response_events():
        yield SimpleNamespace(choices=[])
        yield SimpleNamespace(
            choices=[
                SimpleNamespace(delta=SimpleNamespace(content="first "))
            ]
        )
        yield SimpleNamespace(
            choices=[
                SimpleNamespace(delta=SimpleNamespace(content="second"))
            ]
        )

    provider.client.chat.completions.create = AsyncMock(
        return_value=response_events()
    )

    async def collect():
        return [
            chunk
            async for chunk in provider.generate_stream(
                "hello",
                context="safe context",
                history=[{"role": "assistant", "content": "Earlier answer"}],
            )
        ]

    assert asyncio.run(collect()) == ["first ", "second"]
    call = provider.client.chat.completions.create.await_args
    assert call.kwargs["stream"] is True
    assert call.kwargs["model"] == "test-model"
