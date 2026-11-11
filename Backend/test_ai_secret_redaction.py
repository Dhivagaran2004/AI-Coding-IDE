import pytest

from App.schema.ai_schema import AIChatRequest
from App.service.AI.index.code_chunker import CodeChunker


def test_ai_request_redacts_secrets_from_prompt_context_and_history():
    request = AIChatRequest(
        message="Please debug API_KEY=super-secret-value-1234",
        context=(
            'TOKEN="very-long-sensitive-token-123" '
            "postgresql://user:database-password@host/db"
        ),
        selected_code={
            "file_path": "main.py",
            "language": "python",
            "code": "password = 'sensitive-password-value'",
            "start_line": 1,
            "end_line": 1,
        },
        project_id=1,
        history=[
            {
                "role": "user",
                "content": "Authorization: Bearer "
                "abcdef0123456789abcdef0123456789",
            }
        ],
    )

    serialized = " ".join(
        [
            request.message,
            request.context or "",
            request.selected_code.code if request.selected_code else "",
            *(item.content for item in request.history),
        ]
    )
    assert "super-secret-value-1234" not in serialized
    assert "very-long-sensitive-token-123" not in serialized
    assert "database-password" not in serialized
    assert "sensitive-password-value" not in serialized
    assert "abcdef0123456789abcdef0123456789" not in serialized


def test_repository_indexing_rejects_sensitive_content():
    assert CodeChunker.contains_sensitive_content(
        "API_KEY=super-secret-value-1234"
    )
    assert CodeChunker.contains_sensitive_content(
        "-----BEGIN PRIVATE KEY-----\nkey contents\n-----END PRIVATE KEY-----"
    )
    assert not CodeChunker.contains_sensitive_content(
        "def calculate_total(values):\n    return sum(values)"
    )


def test_runtime_token_generation_is_not_classified_as_a_secret():
    source = (
        "def create_token(user):\n"
        "    access_token = jwt.encode({'sub': user.username}, SECRET_KEY)\n"
        "    return access_token\n"
    )

    assert not CodeChunker.contains_sensitive_content(source)
    assert CodeChunker().chunk_file(
        project_id=1,
        file_id=1,
        file_path="backend/auth.py",
        language="python",
        content=source,
    )


@pytest.mark.parametrize(
    "content",
    [
        "API_KEY='super-secret-value-1234'",
        "password = 'a-hardcoded-password'",
        "ACCESS_TOKEN=abcdef0123456789abcdef",
        "-----BEGIN PRIVATE KEY-----\nkey contents\n-----END PRIVATE KEY-----",
    ],
)
def test_repository_indexing_rejects_hardcoded_credentials(content):
    assert CodeChunker.contains_sensitive_content(content)
