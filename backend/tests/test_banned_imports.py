"""ARCH-21: Ruff's TID251 rule stops the backend from importing demo_company/."""

import subprocess
import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent


def ruff_tid251(source: str) -> subprocess.CompletedProcess[str]:
    # --stdin-filename makes Ruff apply backend/pyproject.toml as if this were a real app file.
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "ruff",
            "check",
            "--no-cache",
            "--select",
            "TID251",
            "--stdin-filename",
            "app/x.py",
            "-",
        ],
        input=source,
        capture_output=True,
        text=True,
        cwd=BACKEND_DIR,
        check=False,
    )


@pytest.mark.parametrize(
    "source",
    [
        "import demo_company\n",
        "import demo_company.seed\n",
        "from demo_company import seed\n",
        "from demo_company.seed import main\n",
    ],
)
def test_demo_company_imports_are_flagged(source: str) -> None:
    result = ruff_tid251(source)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "TID251" in result.stdout
    assert "D-047" in result.stdout


def test_similar_module_name_is_not_flagged() -> None:
    result = ruff_tid251("import demo_companyx\n")
    assert result.returncode == 0, result.stdout + result.stderr
