"""Guards on docs/deploy-dashboard.md (Deploy: OPS-14)."""

import json
import re

from app.cli import _parser
from tests.test_dockerfile import REPO_ROOT

GUIDE = (REPO_ROOT / "docs" / "deploy-dashboard.md").read_text(encoding="utf-8")


def test_the_env_table_lists_exactly_the_names_in_env_example() -> None:
    example = (REPO_ROOT / "frontend" / ".env.example").read_text(encoding="utf-8")
    expected = set(re.findall(r"^([A-Z_]+)=", example, flags=re.MULTILINE))
    table = set(re.findall(r"^\| `([A-Z_]+)` \|", GUIDE, flags=re.MULTILINE))
    assert expected == table
    assert expected
    assert not any(name.startswith("NEXT_PUBLIC_") for name in table)


def test_it_names_release_as_the_production_branch() -> None:
    assert "Branch Tracking -> `release`" in GUIDE


def test_its_region_matches_vercel_json_and_pairs_with_fly() -> None:
    config = json.loads((REPO_ROOT / "frontend" / "vercel.json").read_text(encoding="utf-8"))
    assert config["regions"] == ["iad1"]
    assert '"regions": ["iad1"]' in GUIDE
    fly = (REPO_ROOT / "fly.toml").read_text(encoding="utf-8")
    assert re.search(r'primary_region = "iad"', fly)


def test_its_command_is_real() -> None:
    parser = _parser()
    url = "https://blastproof.in"
    assert f"app.cli dashboard-check --url {url}" in GUIDE
    assert parser.parse_args(["dashboard-check", "--url", url]).command == "dashboard-check"
