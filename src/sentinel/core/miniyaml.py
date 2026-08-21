"""A deliberately small YAML reader for our own prompt files.

There is no runtime dependency in this project, and adding PyYAML to read one
file we write ourselves would be a poor trade. So this parses the subset that
`prompts/prompts.yaml` actually uses and refuses everything else loudly:

* nested mappings, indented with spaces
* sequences of scalars (`- item`)
* plain and quoted scalars, plus `true` / `false` / integers
* literal block scalars (`key: |`), with `|` and `|-` chomping

Not supported, on purpose: anchors, aliases, tags, flow collections, folded
scalars, multiple documents, tab indentation. Every one of those is a way for a
config file to do something surprising, and a prompt file has no business doing
anything surprising. Encountering one raises rather than guessing.

This is a reader for files this repository owns. It is not a general YAML parser
and must not be pointed at untrusted input.
"""

from __future__ import annotations

from typing import Any

from sentinel.core.errors import DataFileError

_TRUE = frozenset({"true", "yes", "on"})
_FALSE = frozenset({"false", "no", "off"})
_UNSUPPORTED_PREFIXES = ("&", "*", "!", "{", "[", ">")


def parse(text: str) -> dict[str, Any]:
    """Parse the supported subset and return the top-level mapping."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    for lineno, line in enumerate(lines, start=1):
        if "\t" in line[: len(line) - len(line.lstrip())]:
            raise DataFileError(f"line {lineno}: tab indentation is not supported")

    index = _skip_blank(lines, 0)
    if index >= len(lines):
        return {}
    value, _ = _parse_mapping(lines, index, _indent_of(lines[index]))
    return value


def _indent_of(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _is_blank(line: str) -> bool:
    stripped = line.strip()
    return not stripped or stripped.startswith("#")


def _skip_blank(lines: list[str], index: int) -> int:
    while index < len(lines) and _is_blank(lines[index]):
        index += 1
    return index


def _scalar(raw: str, lineno: int) -> Any:
    value = raw.strip()
    if not value:
        return ""
    if value[0] in _UNSUPPORTED_PREFIXES:
        raise DataFileError(
            f"line {lineno}: unsupported YAML construct starting with {value[0]!r}."
            " This reader supports mappings, sequences, scalars and literal"
            " block scalars only."
        )
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    lowered = value.lower()
    if lowered in _TRUE:
        return True
    if lowered in _FALSE:
        return False
    if lowered in ("null", "~"):
        return None
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        return value


def _parse_block_scalar(
    lines: list[str], index: int, parent_indent: int, style: str
) -> tuple[str, int]:
    collected: list[str] = []
    block_indent: int | None = None
    while index < len(lines):
        line = lines[index]
        if not line.strip():
            collected.append("")
            index += 1
            continue
        indent = _indent_of(line)
        if indent <= parent_indent:
            break
        if block_indent is None:
            block_indent = indent
        collected.append(line[block_indent:] if len(line) >= block_indent else line.lstrip(" "))
        index += 1

    while collected and not collected[-1]:
        collected.pop()
    text = "\n".join(collected)
    if style == "|" and text:
        text += "\n"
    return text, index


def _parse_sequence(lines: list[str], index: int, indent: int) -> tuple[list[Any], int]:
    items: list[Any] = []
    while index < len(lines):
        if _is_blank(lines[index]):
            index += 1
            continue
        current = _indent_of(lines[index])
        if current < indent:
            break
        if current > indent:
            raise DataFileError(f"line {index + 1}: unexpected indentation inside a sequence")
        stripped = lines[index].strip()
        if not stripped.startswith("- "):
            break
        items.append(_scalar(stripped[2:], index + 1))
        index += 1
    return items, index


def _parse_mapping(lines: list[str], index: int, indent: int) -> tuple[dict[str, Any], int]:
    result: dict[str, Any] = {}
    while index < len(lines):
        if _is_blank(lines[index]):
            index += 1
            continue
        current = _indent_of(lines[index])
        if current < indent:
            break
        if current > indent:
            raise DataFileError(f"line {index + 1}: unexpected indentation in mapping")

        stripped = lines[index].strip()
        if stripped.startswith("- "):
            break
        if ":" not in stripped:
            raise DataFileError(f"line {index + 1}: expected 'key: value', got {stripped!r}")

        key, _, rest = stripped.partition(":")
        key = key.strip()
        rest = rest.strip()
        if not key:
            raise DataFileError(f"line {index + 1}: empty key")
        if key in result:
            raise DataFileError(f"line {index + 1}: duplicate key {key!r}")
        lineno = index + 1
        index += 1

        if rest in ("|", "|-", "|+"):
            value, index = _parse_block_scalar(lines, index, current, "|" if rest == "|" else "|-")
        elif rest == "":
            look = _skip_blank(lines, index)
            if look < len(lines) and _indent_of(lines[look]) > current:
                child_indent = _indent_of(lines[look])
                if lines[look].strip().startswith("- "):
                    value, index = _parse_sequence(lines, look, child_indent)
                else:
                    value, index = _parse_mapping(lines, look, child_indent)
            else:
                value = None
        else:
            value = _scalar(rest, lineno)

        result[key] = value
    return result, index
