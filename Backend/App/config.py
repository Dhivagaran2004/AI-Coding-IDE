from dotenv import load_dotenv
import os

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
DB_SCHEMA = os.getenv("DB_SCHEMA")
SECRET_KEY = os.getenv("SECRET_KEY")
ALGORITHM = os.getenv("ALGORITHM")
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES"))


# LLM_PROVIDER = os.getenv("LLM_PROVIDER", "mock")
# LLM_MODEL = os.getenv("LLM_MODEL", "development")
# LLM_API_KEY = os.getenv("LLM_API_KEY")

# LLM_BASE_URL = os.getenv(
#     "LLM_BASE_URL",
#     "http://localhost:8000/v1",
# )


# AI / LLM configuration
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "mock")

LLM_MODEL = os.getenv(
    "LLM_MODEL",
    "Qwen/Qwen2.5-Coder-32B-Instruct",
)

LLM_API_KEY = os.getenv("LLM_API_KEY")

LLM_BASE_URL = os.getenv(
    "LLM_BASE_URL",
    "https://router.huggingface.co/v1",
)