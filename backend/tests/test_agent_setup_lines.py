"""The lines `POST /v1/agents` returns to connect Claude Code (backend plan §6). No Docker."""

import pytest

from app.config import Settings
from app.services.agent_admin import setup_lines

KEY = "bp_abc123"


def lines(base_url: str) -> tuple[str, str, str]:
    return setup_lines(Settings(_env_file=None, public_base_url=base_url), KEY)


def test_local_address_with_a_port() -> None:
    url, add, env = lines("http://localhost:8000")
    assert url == "http://localhost:8000/mcp"
    assert add == (
        "claude mcp add --transport http blastproof http://localhost:8000/mcp"
        ' --header "Authorization: Bearer bp_abc123"'
    )
    assert env == "BLASTPROOF_AGENT_KEY=bp_abc123"


def test_https_without_a_port() -> None:
    assert lines("https://blastproof.example.com")[0] == "https://blastproof.example.com/mcp"


@pytest.mark.parametrize("base", ["https://b.example.com/", "https://b.example.com"])
def test_a_trailing_slash_is_not_doubled(base: str) -> None:
    assert "//mcp" not in lines(base)[0]
    assert lines(base)[0] == "https://b.example.com/mcp"
