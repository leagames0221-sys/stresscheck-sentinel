"""Shared fixtures and answer builders.

No real people appear in these tests. Respondents are tokens like `t01`, and
reviewers are role strings like `implementer-a`.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence

import pytest

from sentinel.packs.jsq import scoring


@pytest.fixture(autouse=True)
def _clear_data_caches() -> Iterator[None]:
    """Keep cached CSVs from leaking between tests that redirect the data dir."""
    scoring.clear_caches()
    yield
    scoring.clear_caches()


def spread(n_items: int, total: int) -> list[int]:
    """Return `n_items` values in 1..4 summing to `total`.

    Used to hit an exact domain total when what is being tested is the cut-off,
    not the distribution.
    """
    if not n_items <= total <= 4 * n_items:
        raise ValueError(f"total {total} is unreachable with {n_items} items in 1..4")
    values = [1] * n_items
    remaining = total - n_items
    for i in range(n_items):
        step = min(3, remaining)
        values[i] += step
        remaining -= step
    return values


def build_answers(
    variant: str,
    domain_totals: Mapping[str, int],
    omit: Sequence[int] = (),
) -> dict[int, int]:
    """Build raw answers whose *post-reversal* domain sums match `domain_totals`.

    The caller thinks in effective points (what the cut-offs are expressed in);
    this converts back to the raw 1..4 the respondent would have circled,
    inverting the reversal for the items that have it. Building test data by the
    same rule the implementation uses would prove nothing, so the inversion here
    is written independently: `raw = 5 - effective` for reversed items.
    """
    items = scoring.load_items(variant)
    thresholds = scoring.load_thresholds().for_variant(variant)
    answers: dict[int, int] = {}

    for domain, total in domain_totals.items():
        domain_items = [i for i in items if i.domain == domain and i.scored]
        for item, effective in zip(domain_items, spread(len(domain_items), total), strict=True):
            answers[item.item_no] = (
                5 - effective if item.item_no in thresholds.reverse_items else effective
            )

    for item_no in omit:
        answers.pop(item_no, None)
    return answers


@pytest.fixture
def prompts_file(tmp_path):
    """A minimal prompts.yaml written to a temp dir."""
    path = tmp_path / "prompts.yaml"
    path.write_text(
        "version: 1\n"
        "prompts:\n"
        "  selfcare_general:\n"
        "    system: |\n"
        "      あなたはセルフケアの情報を伝える補助ツールです。\n"
        "      診断はしません。\n"
        "    user: |\n"
        "      領域Bの合計は {b_sum} でした。\n"
        "    fallback_text: |\n"
        "      この結果は診断ではありません。相談窓口: #いのちSOS 0120-061-338\n"
        "    required_tokens:\n"
        "      - 診断ではありません\n"
        "      - 0120-061-338\n"
        "  no_fallback:\n"
        "    system: sys\n"
        "    user: usr\n",
        encoding="utf-8",
    )
    return path
