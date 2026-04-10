"""Tests for the Kronvex CLI."""
from click.testing import CliRunner
from unittest.mock import patch, MagicMock
from kronvex.cli import cli


def _make_client_mock(**kwargs):
    """Return a MagicMock pre-configured for the Kronvex client."""
    mock_client = MagicMock()
    for attr, val in kwargs.items():
        getattr(mock_client, attr).return_value = val
    return mock_client


def test_agents_list():
    runner = CliRunner()
    mock_client = _make_client_mock(list_agents=[{"id": "abc", "name": "test-agent"}])
    with patch("kronvex.cli.Kronvex", return_value=mock_client):
        result = runner.invoke(cli, ["--api-key", "kv-test", "agents", "list"])
    assert result.exit_code == 0, result.output
    assert "abc" in result.output


def test_remember():
    runner = CliRunner()
    mock_agent = MagicMock()
    mock_agent.remember.return_value = {"id": "mem-1", "content": "hello"}
    mock_client = MagicMock()
    mock_client.agent.return_value = mock_agent
    with patch("kronvex.cli.Kronvex", return_value=mock_client):
        result = runner.invoke(cli, ["--api-key", "kv-test", "remember", "agent-1", "hello"])
    assert result.exit_code == 0, result.output
    assert "mem-1" in result.output


def test_recall():
    runner = CliRunner()
    mock_agent = MagicMock()
    mock_agent.recall.return_value = [{"id": "m1", "content": "hello"}]
    mock_client = MagicMock()
    mock_client.agent.return_value = mock_agent
    with patch("kronvex.cli.Kronvex", return_value=mock_client):
        result = runner.invoke(cli, ["--api-key", "kv-test", "recall", "agent-1", "hello"])
    assert result.exit_code == 0, result.output
    assert "m1" in result.output


def test_memories_list():
    runner = CliRunner()
    mock_agent = MagicMock()
    mock_agent.memories.return_value = [{"id": "m2", "content": "stored memory"}]
    mock_client = MagicMock()
    mock_client.agent.return_value = mock_agent
    with patch("kronvex.cli.Kronvex", return_value=mock_client):
        result = runner.invoke(cli, ["--api-key", "kv-test", "memories", "list", "agent-1"])
    assert result.exit_code == 0, result.output
    assert "m2" in result.output


def test_agent_create():
    runner = CliRunner()
    mock_agent = MagicMock()
    mock_agent.id = "new-agent-id"
    mock_agent.to_dict.return_value = {"id": "new-agent-id", "name": "my-agent"}
    mock_client = MagicMock()
    mock_client.create_agent.return_value = mock_agent
    with patch("kronvex.cli.Kronvex", return_value=mock_client):
        result = runner.invoke(cli, ["--api-key", "kv-test", "agent", "create", "my-agent"])
    assert result.exit_code == 0, result.output
    assert "new-agent-id" in result.output


def test_agent_delete():
    runner = CliRunner()
    mock_client = MagicMock()
    with patch("kronvex.cli.Kronvex", return_value=mock_client):
        result = runner.invoke(cli, ["--api-key", "kv-test", "agent", "delete", "agent-1"])
    assert result.exit_code == 0, result.output
    mock_client.delete_agent.assert_called_once_with("agent-1")


def test_missing_api_key():
    runner = CliRunner()
    result = runner.invoke(cli, ["agents", "list"], env={"KRONVEX_API_KEY": ""})
    assert result.exit_code != 0
