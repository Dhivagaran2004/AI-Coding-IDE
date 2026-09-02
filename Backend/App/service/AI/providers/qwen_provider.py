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
                    "debug, and improve code."
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