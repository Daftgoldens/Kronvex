from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Database
    database_url: str = "postgresql+asyncpg://memory:memory@localhost:5432/agentmemory"

    # OpenAI
    openai_api_key: str = "sk-..."
    embedding_model: str = "text-embedding-3-small"
    embedding_dimensions: int = 1536

    # Sentry
    sentry_dsn: str = ""

    # Memory
    default_top_k: int = 5
    default_similarity_threshold: float = 0.75
    max_memories_per_agent: int = 10_000

    # Admin
    admin_token: str = ""


settings = Settings()
