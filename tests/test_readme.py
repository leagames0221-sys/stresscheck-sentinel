"""The README is checked, not trusted.

A README is the one file that is read by everyone and tested by no one, so it
rots first: a subcommand gets renamed, a link points at a file that moved, an
environment variable is documented under a name the code never reads. Each of
those is a small lie that survives every other test in this repository.

These tests are deliberately mechanical. They do not judge whether the prose is
accurate — no test can — but they do refuse to let the README name a CLI
subcommand, a repository file or an environment variable that does not exist.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from sentinel import cli

REPO_ROOT = Path(__file__).resolve().parents[1]
README = REPO_ROOT / "README.md"


@pytest.fixture(scope="module")
def readme_text() -> str:
    return README.read_text(encoding="utf-8")


def _subcommands() -> set[str]:
    parser = cli.build_parser()
    actions = [a for a in parser._subparsers._group_actions if hasattr(a, "choices")]  # type: ignore[union-attr]
    assert actions, "the CLI parser no longer exposes subcommands"
    return set(actions[0].choices)


def test_readme_exists_and_is_utf8(readme_text: str):
    assert readme_text.strip()
    # A mojibake'd README would still 'pass' most checks, so assert a known
    # Japanese heading survives the round trip.
    assert "## Quickstart" in readme_text
    assert "職業性ストレス簡易調査票" in readme_text


def test_every_cli_command_shown_in_the_readme_exists(readme_text: str):
    shown = set(re.findall(r"python -m sentinel\.cli (?:(--\w[\w-]*)|([a-z][a-z-]*))", readme_text))
    named = {sub for _flag, sub in shown if sub}
    assert named, "the README no longer shows any CLI subcommand"
    unknown = named - _subcommands()
    assert not unknown, f"README documents subcommands that do not exist: {sorted(unknown)}"


def test_every_cli_subcommand_is_documented(readme_text: str):
    """The other direction: a command nobody mentions is a command nobody finds."""
    undocumented = {sub for sub in _subcommands() if f"sentinel.cli {sub}" not in readme_text}
    assert not undocumented, f"CLI subcommands missing from the README: {sorted(undocumented)}"


def test_relative_links_point_at_files_that_exist(readme_text: str):
    targets = re.findall(r"\]\((?!https?://|#)([^)]+)\)", readme_text)
    assert targets, "the README no longer links to anything in the repository"
    missing = [t for t in targets if not (REPO_ROOT / t.split("#", 1)[0]).exists()]
    assert not missing, f"README links to files that do not exist: {missing}"


def test_every_adr_is_listed(readme_text: str):
    adrs = sorted(p.name for p in (REPO_ROOT / "docs" / "adr").glob("ADR-*.md"))
    assert len(adrs) == 7, adrs
    missing = [name for name in adrs if name not in readme_text]
    assert not missing, f"ADRs not linked from the README: {missing}"


@pytest.mark.parametrize("variable", ["LLM_PROVIDER", "OLLAMA_MODEL", "LLM_PROMPTS_PATH"])
def test_documented_environment_variables_are_read_by_the_code(readme_text: str, variable: str):
    assert variable in readme_text, f"{variable} is no longer documented"
    sources = (REPO_ROOT / "src").rglob("*.py")
    assert any(variable in path.read_text(encoding="utf-8") for path in sources), (
        f"the README documents {variable}, but no source file reads it"
    )


def test_the_zero_dependency_claim_is_true_of_the_source(readme_text: str):
    """The README's loudest claim, checked against every import in `src/`.

    `dependencies = []` in pyproject.toml only says what is *declared*. This
    walks the AST of every module and fails if anything outside the standard
    library is imported, which is the claim a reader actually cares about.
    """
    import ast
    import sys

    assert "ランタイム依存 **0**" in readme_text

    stdlib = set(sys.stdlib_module_names)
    foreign: dict[str, str] = {}
    for path in (REPO_ROOT / "src").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module]
            else:
                continue
            for name in names:
                top = name.split(".")[0]
                if top not in stdlib and top != "sentinel":
                    foreign[top] = str(path.relative_to(REPO_ROOT))

    assert not foreign, f"non-stdlib imports found in src/: {foreign}"


def test_the_default_port_in_the_readme_matches_the_server(readme_text: str):
    from sentinel.app.server import BIND_HOST, DEFAULT_PORT

    assert f"http://{BIND_HOST}:{DEFAULT_PORT}" in readme_text


def test_the_version_shown_in_the_readme_matches_the_package(readme_text: str):
    from sentinel import __version__

    assert f"sentinel {__version__}" in readme_text
