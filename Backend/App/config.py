from dotenv import load_dotenv
import os
import math

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

DATABASE_URL = os.getenv("DATABASE_URL")
DB_SCHEMA = os.getenv("DB_SCHEMA")
SECRET_KEY = os.getenv("SECRET_KEY")
ALGORITHM = os.getenv("ALGORITHM")
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES"))


# Groq chat configuration
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "groq")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-20b")
LLM_MODEL = GROQ_MODEL

# Legacy Hugging Face credential used only by repository embeddings.
LLM_API_KEY = os.getenv("LLM_API_KEY")

AI_DAILY_REQUEST_LIMIT = int(os.getenv("AI_DAILY_REQUEST_LIMIT", "120"))
AI_DAILY_TOKEN_LIMIT = int(os.getenv("AI_DAILY_TOKEN_LIMIT", "250000"))
AI_OUTPUT_TOKEN_RESERVATION = int(
    os.getenv("AI_OUTPUT_TOKEN_RESERVATION", "512")
)
AI_CONTEXT_MAX_CHARS = int(os.getenv("AI_CONTEXT_MAX_CHARS", "100000"))
AI_MAX_RETRIES = int(os.getenv("AI_MAX_RETRIES", "2"))
AI_RETRY_BASE_DELAY = float(os.getenv("AI_RETRY_BASE_DELAY", "0.25"))
AI_REQUEST_TIMEOUT_SECONDS = int(os.getenv("AI_REQUEST_TIMEOUT_SECONDS", "60"))
AI_INDEX_MAX_FILE_CHARS = int(os.getenv("AI_INDEX_MAX_FILE_CHARS", "500000"))
AI_AGENT_MAX_TOOL_CALLS = int(os.getenv("AI_AGENT_MAX_TOOL_CALLS", "8"))

if min(
    AI_DAILY_REQUEST_LIMIT,
    AI_DAILY_TOKEN_LIMIT,
    AI_OUTPUT_TOKEN_RESERVATION,
    AI_MAX_RETRIES,
    AI_REQUEST_TIMEOUT_SECONDS,
    AI_INDEX_MAX_FILE_CHARS,
    AI_AGENT_MAX_TOOL_CALLS,
) < 0:
    raise ValueError("AI limits and retry settings cannot be negative.")
if AI_CONTEXT_MAX_CHARS <= 0 or AI_INDEX_MAX_FILE_CHARS == 0:
    raise ValueError("AI context and file size limits must be greater than zero.")
if AI_MAX_RETRIES > 10:
    raise ValueError("AI_MAX_RETRIES cannot exceed 10.")
if AI_REQUEST_TIMEOUT_SECONDS < 1 or AI_REQUEST_TIMEOUT_SECONDS > 300:
    raise ValueError("AI_REQUEST_TIMEOUT_SECONDS must be between 1 and 300.")
if AI_AGENT_MAX_TOOL_CALLS < 1 or AI_AGENT_MAX_TOOL_CALLS > 20:
    raise ValueError("AI_AGENT_MAX_TOOL_CALLS must be between 1 and 20.")
if not math.isfinite(AI_RETRY_BASE_DELAY) or AI_RETRY_BASE_DELAY < 0:
    raise ValueError("AI_RETRY_BASE_DELAY must be a finite non-negative number.")