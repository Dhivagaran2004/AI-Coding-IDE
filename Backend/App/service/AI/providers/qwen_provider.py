from collections.abc import AsyncIterator

from openai import AsyncOpenAI

from App.service.AI.providers.base_provider import BaseLLMProvider


class QwenProvider(BaseLLMProvider):
    """
    Qwen provider using Hugging Face Inference Providers.
    """

    def __init__(
        self,
        model: str,
        api_key: str | None = None,
        base_url: str | None = None,
    ):
        self.model = model

        self.client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
        )

    async def generate(
        self,
        message: str,
        context: str | None = None,
        history: list[dict[str, str]] | None = None,
    ) -> str:
        """
        Generate a response from Qwen through
        Hugging Face Inference Providers.
        """

        messages: list[dict[str, str]] = [
            {
                "role": "system",
                "content": (
                    "You are an AI coding assistant inside "
                    "a developer IDE. "

                    "Help the user write, understand, "
                    "debug, refactor, and improve code. "

                    "When the user asks you to generate code, "
                    "provide a clear and complete solution. "

                    "When appropriate, use Markdown code blocks "
                    "with the correct programming language. "

                    "Do not invent files or project information "
                    "that is not present in the provided context."

                    "When asked for a structured plan, follow its requested JSON "
                    "schema exactly and include one separate action entry for every "
                    "changed file in the requested actions array. For direct IDE "
                    "change requests, include one or more fenced JSON code actions "
                    "with type code_change, "
                    "operation replace, file_path, 1-based start_line and end_line, "
                    "old_code copied exactly from context, new_code, and description. "
                    "For a change spanning files, return a separate action for every "
                    "changed file, preserve the imports/calls that connect supporting "
                    "files to the main or entry-point file, and describe the links. "
                    "Do not emit an action if exact old_code or its line range is "
                    "uncertain. Never apply changes; the user must approve them."
                ),
            }
        ]

        # Add previous conversation messages
        if history:
            messages.extend(history)

        # Add IDE context to the current request
        user_message = message

        if context:
            user_message = (
                f"Code or IDE context:\n"
                f"{context}\n\n"
                f"User request:\n"
                f"{message}"
            )

        messages.append(
            {
                "role": "user",
                "content": user_message,
            }
        )

        response = await self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=0.2,
        )

        return response.choices[0].message.content or ""

    async def generate_stream(
        self,
        message: str,
        context: str | None = None,
        history: list[dict[str, str]] | None = None,
    ) -> AsyncIterator[str]:
        messages: list[dict[str, str]] = [
            {
                "role": "system",
                "content": (
                    "You are an AI coding assistant inside a developer IDE. "
                    "Help the user write, understand, debug, refactor, and improve code. "
                    "When the user asks you to generate code, provide a clear and complete "
                    "solution. Use Markdown code blocks when appropriate. Do not invent "
                    "files or project information that is not present in the provided "
                    "context. When asked for a structured plan, follow its requested "
                    "JSON schema exactly and include one separate action entry for "
                    "every changed file in the requested actions array. For direct IDE "
                    "change requests, include one or more fenced JSON code actions "
                    "with type code_change, "
                    "operation replace, file_path, 1-based start_line and end_line, "
                    "old_code copied exactly from context, new_code, and description. "
                    "For a change spanning files, return a separate action for every "
                    "changed file, preserve the imports/calls that connect supporting "
                    "files to the main or entry-point file, and describe the links. "
                    "Do not emit an action if exact old_code or its line range is "
                    "uncertain. Never apply changes; the user must approve them."
                ),
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

        response = await self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=0.2,
            stream=True,
        )
        async for event in response:
            if not event.choices:
                continue
            content = event.choices[0].delta.content
            if isinstance(content, str) and content:
                yield content