from collections.abc import AsyncIterator

from openai import AsyncOpenAI

from App.config import AI_REQUEST_TIMEOUT_SECONDS
from App.service.AI.providers.base_provider import BaseLLMProvider


class GroqProvider(BaseLLMProvider):
    """Groq chat completions over its OpenAI-compatible API."""

    BASE_URL = "https://api.groq.com/openai/v1"
    REQUEST_TIMEOUT_SECONDS = AI_REQUEST_TIMEOUT_SECONDS

    def __init__(self, model: str, api_key: str):
        if not api_key:
            raise ValueError("GROQ_API_KEY is required.")
        self.model = model
        self.client = AsyncOpenAI(
            api_key=api_key,
            base_url=self.BASE_URL,
            timeout=self.REQUEST_TIMEOUT_SECONDS,
            max_retries=0,
        )

    async def generate_json(
        self,
        message: str,
        context: str | None = None,
        history: list[dict[str, str]] | None = None,
    ) -> str:
        try:
            return await self.generate(
                message,
                context=context,
                history=history,
                json_mode=True,
            )
        except Exception as error:
            if getattr(error, "code", None) != "json_validate_failed":
                raise
            return await self.generate(
                message,
                context=context,
                history=history,
            )

    @staticmethod
    def _messages(
        message: str,
        context: str | None,
        history: list[dict[str, str]] | None,
        *,
        json_mode: bool = False,
    ) -> list[dict[str, str]]:
        if json_mode:
            system_content = (
                "You are a coding agent for a developer IDE. Use only the provided "
                "project context and validated backend tools. Return exactly one "
                "JSON object and no Markdown, code fences, or surrounding prose. "
                "A tool request has type=\"tool_call\", name, and arguments. A final "
                "plan has plan (an array of concise strings), actions (an array of "
                "code changes), and validation_command (a string or null). Replace "
                "actions must include type=\"code_change\", operation=\"replace\", "
                "file_path, description, 1-based start_line and end_line, old_code "
                "copied exactly from context, and new_code. New files may use "
                "operation=\"create_file\". Never invent file paths or old_code. "
                "Do not claim to have changed files. File changes and commands "
                "require user approval."
            )
        else:
            system_content = (
                "You are an AI coding assistant inside a developer IDE. "
                "Help the user write, understand, debug, refactor, and improve code. "
                "When the user asks you to generate code, provide a clear and complete "
                "solution. Use Markdown code blocks when appropriate. Do not invent "
                "files or project information that is not present in the provided "
                "context. When asked for a structured plan, follow its requested JSON "
                "schema exactly and include one separate action entry for every changed "
                "file in the requested actions array. For direct IDE change requests, "
                "include one or more fenced JSON code actions with type code_change, "
                "operation replace, file_path, 1-based start_line and end_line, old_code "
                "copied exactly from context, new_code, and description. For a change "
                "spanning files, return a separate action for every changed file, "
                "preserve imports/calls that connect supporting files to the main or "
                "entry-point file, and describe those links. Do not emit an action if "
                "exact old_code or its line range is uncertain. Never apply changes; "
                "the user must approve them."
            )
        messages = [
            {
                "role": "system",
                "content": system_content,
            }
        ]
        if history:
            messages.extend(history)

        user_message = message
        if context:
            user_message = (
                f"Code or IDE context:\n{context}\n\nUser request:\n{message}"
            )
        messages.append({"role": "user", "content": user_message})
        return messages

    async def generate(
        self,
        message: str,
        context: str | None = None,
        history: list[dict[str, str]] | None = None,
        *,
        json_mode: bool = False,
    ) -> str:
        request_options = (
            {"response_format": {"type": "json_object"}}
            if json_mode
            else {}
        )
        response = await self.client.chat.completions.create(
            model=self.model,
            messages=self._messages(
                message,
                context,
                history,
                json_mode=json_mode,
            ),
            temperature=0.2,
            **request_options,
        )
        return response.choices[0].message.content or ""

    async def generate_stream(
        self,
        message: str,
        context: str | None = None,
        history: list[dict[str, str]] | None = None,
    ) -> AsyncIterator[str]:
        response = await self.client.chat.completions.create(
            model=self.model,
            messages=self._messages(message, context, history),
            temperature=0.2,
            stream=True,
        )
        async for event in response:
            if not event.choices:
                continue
            content = event.choices[0].delta.content
            if isinstance(content, str) and content:
                yield content
