# tests/test_webhooks_b2.py
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import httpx
import app.webhooks  # ensure module is in sys.modules before patches resolve


@pytest.fixture
def mock_webhook_config():
    cfg = MagicMock()
    cfg.url = "https://example.com/hook"
    cfg.secret = "test-secret"
    cfg.events = ["memory.recalled"]
    return cfg


@pytest.fixture
def mock_db():
    """Kept for backward compat — fire_webhook_event accepts but no longer uses it."""
    return AsyncMock()


def _make_session_patch(mock_webhook_config):
    """Return a context-manager patch for app.webhooks.AsyncSessionLocal that yields
    a fake session whose execute() returns the given config."""
    result = MagicMock()
    result.scalars.return_value.all.return_value = [mock_webhook_config]

    fake_session = AsyncMock()
    fake_session.execute = AsyncMock(return_value=result)
    fake_session.__aenter__ = AsyncMock(return_value=fake_session)
    fake_session.__aexit__ = AsyncMock(return_value=False)

    MockSessionLocal = MagicMock(return_value=fake_session)
    return patch("app.webhooks.AsyncSessionLocal", MockSessionLocal)


@pytest.mark.asyncio
async def test_fire_webhook_retries_on_500(mock_db, mock_webhook_config):
    """Should retry up to 3 attempts when server returns 500."""
    import uuid
    api_key_id = uuid.uuid4()

    call_count = 0

    async def fake_post(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        r = MagicMock()
        r.status_code = 500
        return r

    with _make_session_patch(mock_webhook_config):
        with patch("app.webhooks.AsyncWebhookClient") as MockClient:
            instance = AsyncMock()
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            instance.post = fake_post
            MockClient.return_value = instance

            with patch("app.webhooks.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
                from app.webhooks import fire_webhook_event
                await fire_webhook_event("memory.recalled", api_key_id, mock_db, {"agent_id": "abc"})
                assert call_count == 3
                assert mock_sleep.call_count == 2  # sleep after attempt 0 and 1 (not after final attempt)


@pytest.mark.asyncio
async def test_fire_webhook_no_retry_on_200(mock_db, mock_webhook_config):
    """Should stop after first successful 200 — no retry."""
    import uuid
    api_key_id = uuid.uuid4()
    call_count = 0

    async def fake_post(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        r = MagicMock()
        r.status_code = 200
        return r

    with _make_session_patch(mock_webhook_config):
        with patch("app.webhooks.AsyncWebhookClient") as MockClient:
            instance = AsyncMock()
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            instance.post = fake_post
            MockClient.return_value = instance

            from app.webhooks import fire_webhook_event
            await fire_webhook_event("memory.recalled", api_key_id, mock_db, {})

    assert call_count == 1


@pytest.mark.asyncio
async def test_fire_webhook_no_retry_on_4xx(mock_db, mock_webhook_config):
    """4xx = client error, should not retry."""
    import uuid
    api_key_id = uuid.uuid4()
    call_count = 0

    async def fake_post(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        r = MagicMock()
        r.status_code = 400
        return r

    with _make_session_patch(mock_webhook_config):
        with patch("app.webhooks.AsyncWebhookClient") as MockClient:
            instance = AsyncMock()
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            instance.post = fake_post
            MockClient.return_value = instance

            from app.webhooks import fire_webhook_event
            await fire_webhook_event("memory.recalled", api_key_id, mock_db, {})

    assert call_count == 1


@pytest.mark.asyncio
async def test_fire_webhook_skips_unmatched_events(mock_db, mock_webhook_config):
    """Should not call HTTP if no configs subscribe to the fired event."""
    import uuid
    api_key_id = uuid.uuid4()
    mock_webhook_config.events = ["memory.stored"]  # not memory.recalled

    with _make_session_patch(mock_webhook_config):
        with patch("app.webhooks.AsyncWebhookClient") as MockClient:
            instance = AsyncMock()
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = instance

            from app.webhooks import fire_webhook_event
            await fire_webhook_event("memory.recalled", api_key_id, mock_db, {})

    instance.post.assert_not_called()
