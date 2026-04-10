import ssl
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy import text

from app.config import settings


def _fix_database_url(url: str) -> str:
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
    elif url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql+asyncpg://", 1)
    url = url.split("?")[0]
    return url


_db_url = _fix_database_url(settings.database_url)

# Supabase requires SSL but uses a self-signed cert in its chain —
# CERT_NONE is intentional here, traffic is still encrypted
_ssl = ssl.create_default_context()
_ssl.check_hostname = False
_ssl.verify_mode = ssl.CERT_NONE

engine = create_async_engine(
    _db_url,
    echo=False,
    pool_size=5,
    max_overflow=10,
    pool_recycle=3600,
    connect_args={"ssl": _ssl},
)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session


async def init_db():
    """Create tables, enable pgvector, and create daily usage table + function."""
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.run_sync(Base.metadata.create_all, checkfirst=True)
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS daily_api_usage (
                api_key_id  UUID    NOT NULL,
                date        DATE    NOT NULL DEFAULT CURRENT_DATE,
                recall_count    INTEGER DEFAULT 0,
                remember_count  INTEGER DEFAULT 0,
                inject_count    INTEGER DEFAULT 0,
                total_tokens    INTEGER DEFAULT 0,
                PRIMARY KEY (api_key_id, date),
                FOREIGN KEY (api_key_id) REFERENCES api_keys(id) ON DELETE CASCADE
            )
        """))
        await conn.execute(text("""
            CREATE OR REPLACE FUNCTION increment_daily_usage(
                p_key_id  UUID,
                p_endpoint VARCHAR,
                p_tokens   INTEGER
            ) RETURNS VOID AS $$
            BEGIN
                INSERT INTO daily_api_usage
                    (api_key_id, date, recall_count, remember_count, inject_count, total_tokens)
                VALUES (
                    p_key_id, CURRENT_DATE,
                    CASE WHEN p_endpoint = 'recall'   THEN 1 ELSE 0 END,
                    CASE WHEN p_endpoint = 'remember' THEN 1 ELSE 0 END,
                    CASE WHEN p_endpoint = 'inject'   THEN 1 ELSE 0 END,
                    p_tokens
                )
                ON CONFLICT (api_key_id, date) DO UPDATE SET
                    recall_count   = daily_api_usage.recall_count   + EXCLUDED.recall_count,
                    remember_count = daily_api_usage.remember_count + EXCLUDED.remember_count,
                    inject_count   = daily_api_usage.inject_count   + EXCLUDED.inject_count,
                    total_tokens   = daily_api_usage.total_tokens   + EXCLUDED.total_tokens;
            END;
            $$ LANGUAGE plpgsql;
        """))
