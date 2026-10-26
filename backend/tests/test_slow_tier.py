"""The `slow` tier (D-190) only helps local work: it must never remove a test from CI."""

from pathlib import Path

from tests.conftest import SLOW_FILES

TESTS = Path(__file__).parent
CI_FILE = TESTS.parents[1] / ".github" / "workflows" / "ci.yml"


def test_every_slow_file_exists() -> None:
    """A renamed file would quietly leave the list (and the guard below would not notice)."""
    assert [name for name in sorted(SLOW_FILES) if not (TESTS / name).exists()] == []


def test_ci_never_skips_slow_tests() -> None:
    assert "--skip-slow" not in CI_FILE.read_text(encoding="utf-8")


def test_the_slow_files_hold_integration_tests() -> None:
    """A slow file with only unit tests would be skipped for nothing (and --skip-slow is a no-op
    on a plain `pytest` run, which already leaves integration out)."""
    for name in SLOW_FILES:
        assert "pytest.mark.integration" in (TESTS / name).read_text(encoding="utf-8"), name
