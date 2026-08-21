"""The fixed response shown instead of a generated one.

When the classifier fires, the respondent sees text assembled here from
`data/hotlines_ja.csv` — not text from a model, and not text a model was asked
to check. There is no code path from a crisis classification into generation.

Wording is fixed for the same reason the helplines are: a person reading this
screen should get the number, and nothing between them and the number should be
novel each time.
"""

from __future__ import annotations

import functools
from dataclasses import dataclass
from pathlib import Path

from sentinel.core.datafiles import data_path, read_rows
from sentinel.core.errors import DataFileError
from sentinel.core.gates import require_non_empty

HOTLINES_FILE = "hotlines_ja.csv"

#: Shown above the helplines, for every detected stage. Deliberately short, and
#: deliberately free of anything a lint rule could object to: no condition
#: names, no likelihoods, no instruction to seek treatment.
CRISIS_HEADLINE = "つらい状況が続いているようでしたら、一人で抱えずに相談できる窓口があります。"

CRISIS_FOOTER = (
    "この画面は自動的な文面判定によって表示されています。"
    "内容の判定に AI は使用しておらず、医学的な診断ではありません。"
    "すぐに危険が迫っている場合は 119 番へ連絡してください。"
)


@dataclass(frozen=True)
class Hotline:
    """One published helpline."""

    id: str
    name: str
    phone: str
    alt_phone: str
    hours: str
    scope: str
    url: str
    fetched_on: str

    def as_dict(self) -> dict[str, str]:
        return {
            "id": self.id,
            "name": self.name,
            "phone": self.phone,
            "alt_phone": self.alt_phone,
            "hours": self.hours,
            "scope": self.scope,
            "url": self.url,
            "fetched_on": self.fetched_on,
        }

    def as_line(self) -> str:
        phones = self.phone if not self.alt_phone else f"{self.phone}（{self.alt_phone}）"
        return f"・{self.name}　{phones}　{self.hours}"


def load_hotlines(csv_path: Path | str | None = None) -> tuple[Hotline, ...]:
    """Load the helpline table.

    Raises:
        DataFileError: the file is missing or a row lacks a name or number.
        GateSetupError: the file parsed but has no rows — a crisis response with
            no helpline in it is the empty-dictionary failure again.
    """
    path = Path(csv_path) if csv_path is not None else data_path(HOTLINES_FILE)
    if not path.is_file():
        raise DataFileError(f"missing helpline file: {path}")

    rows: list[Hotline] = []
    for row in read_rows(path):
        hotline_id = row.get("id", "").strip()
        name = row.get("name", "").strip()
        phone = row.get("phone", "").strip()
        if not (hotline_id or name or phone):
            continue
        if not (name and phone):
            raise DataFileError(f"{path.name}: helpline {hotline_id!r} lacks a name or a number")
        rows.append(
            Hotline(
                id=hotline_id,
                name=name,
                phone=phone,
                alt_phone=row.get("alt_phone", "").strip(),
                hours=row.get("hours", "").strip(),
                scope=row.get("scope", "").strip(),
                url=row.get("url", "").strip(),
                fetched_on=row.get("fetched_on", "").strip(),
            )
        )

    require_non_empty("crisis_response", rows)
    return tuple(rows)


@functools.cache
def _cached_hotlines() -> tuple[Hotline, ...]:
    return load_hotlines()


def clear_caches() -> None:
    """Drop the cached helpline table. Used by tests that redirect the data dir."""
    _cached_hotlines.cache_clear()


def fixed_response(level: str, hotlines: tuple[Hotline, ...] | None = None) -> dict[str, object]:
    """Build the deterministic crisis response for a detected stage.

    Args:
        level: the classified stage. Present in the payload so the reviewer
            screen can show it; it does not change the wording, because a
            respondent at `explore` who is shown a shorter message than one at
            `plan` is being triaged by a text generator.

    Returns:
        A dict with `headline`, `hotlines` (structured, for rendering) and
        `text` (the same content as plain text, for the CLI and for lint).
    """
    rows = hotlines if hotlines is not None else _cached_hotlines()
    body = "\n".join(h.as_line() for h in rows)
    return {
        "level": level,
        "headline": CRISIS_HEADLINE,
        "hotlines": [h.as_dict() for h in rows],
        "source_url": rows[0].url,
        "text": f"{CRISIS_HEADLINE}\n{body}\n{CRISIS_FOOTER}",
        "generated_by": "fixed_text",
    }
