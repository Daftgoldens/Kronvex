import time
from collections import OrderedDict

from openai import AsyncOpenAI

from app.config import settings

_client: AsyncOpenAI | None = None

# LRU cache: {text -> (vector, timestamp)}
_embed_cache: OrderedDict[str, tuple[list[float], float]] = OrderedDict()
_CACHE_MAX_SIZE = 512
_CACHE_TTL_SECONDS = 3600  # 1 hour


def get_openai_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(
            api_key=settings.openai_api_key,
            max_retries=3,          # auto-retry on 429 / 5xx with exponential backoff
            timeout=10.0,           # per-request timeout — max ~35s total with 3 retries
        )
    return _client


async def embed(text: str) -> list[float]:
    """Embed a single text string. Returns a list of floats.

    Results are cached in-process (LRU, max 512 entries, 1h TTL)
    to avoid redundant OpenAI calls for identical queries.
    """
    text = text.replace("\n", " ").strip()
    now = time.monotonic()

    # Cache hit
    if text in _embed_cache:
        vector, ts = _embed_cache[text]
        if now - ts < _CACHE_TTL_SECONDS:
            _embed_cache.move_to_end(text)  # LRU refresh
            return vector
        else:
            del _embed_cache[text]

    # Cache miss — call OpenAI
    client = get_openai_client()
    response = await client.embeddings.create(
        input=text,
        model=settings.embedding_model,
    )
    vector = response.data[0].embedding

    # Evict oldest entry if at capacity
    if len(_embed_cache) >= _CACHE_MAX_SIZE:
        _embed_cache.popitem(last=False)

    _embed_cache[text] = (vector, now)
    return vector


async def embed_batch(texts: list[str]) -> list[list[float]]:
    """Embed multiple texts in a single API call (cheaper + faster)."""
    client = get_openai_client()
    cleaned = [t.replace("\n", " ").strip() for t in texts]
    response = await client.embeddings.create(
        input=cleaned,
        model=settings.embedding_model,
    )
    # Response is sorted by index
    return [item.embedding for item in sorted(response.data, key=lambda x: x.index)]
