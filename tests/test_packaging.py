"""Release hygiene: package metadata must match the code and the CI matrix."""
import tomllib
from pathlib import Path

import regressagent

ROOT = Path(__file__).resolve().parent.parent
PROJECT = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
META = PROJECT["project"]


def test_version_matches_the_package():
    assert META["version"] == regressagent.__version__


def test_license_is_declared_and_shipped():
    assert META["license"] == "MIT"
    assert META["license-files"] == ["LICENSE"]
    assert (ROOT / "LICENSE").read_text(encoding="utf-8").startswith("MIT License")
    # the SPDX `license` expression needs setuptools >= 77
    assert any(r.replace(" ", "").startswith("setuptools>=77") for r in PROJECT["build-system"]["requires"])


def test_classifiers_cover_the_ci_matrix():
    classifiers = set(META["classifiers"])
    workflow = (ROOT / ".github" / "workflows" / "tests.yml").read_text(encoding="utf-8")
    for version in ("3.11", "3.12"):
        assert f'"{version}"' in workflow  # still in the CI matrix
        assert f"Programming Language :: Python :: {version}" in classifiers
    assert "Development Status :: 3 - Alpha" in classifiers
    assert META["requires-python"] == ">=3.11"


def test_project_urls_point_at_the_repository():
    assert META["urls"]["Repository"] == "https://github.com/abrehmanmiqbal/AgentProof"
    assert META["urls"]["Documentation"].startswith(META["urls"]["Repository"])


def test_no_claims_we_cannot_back():
    classifiers = " ".join(META["classifiers"])
    assert "Typing :: Typed" not in classifiers  # no py.typed marker is shipped
    assert "Operating System :: OS Independent" not in classifiers  # only tested on Linux


def test_readme_warns_that_the_old_pypi_name_belongs_to_another_project():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "pip install agentproof" in readme and "different project" in readme


def test_release_notes_draft_exists_and_is_honest():
    notes = (ROOT / "docs" / "RELEASE_NOTES_0.1.0.md").read_text(encoding="utf-8")
    assert notes.startswith("# RegressAgent 0.1.0")
    assert "DRAFT" in notes and "not published" in notes.lower()
    assert "not a sandbox" in notes.lower()  # Tier A must never be oversold
    assert META["version"] in notes
    for section in ("## What it does", "## Install", "## Known limitations", "## Security notes"):
        assert section in notes
