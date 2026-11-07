"""Minimal deterministic TOML writer for the W2U data shapes (tables, arrays of tables, scalars, arrays).

``tomllib.loads(dumps(value)) == value`` for every value this project writes.
"""

from __future__ import annotations

import re

_BARE = re.compile(r"[A-Za-z0-9_-]+")


def _key(key: str) -> str:
    if _BARE.fullmatch(key):
        return key
    return '"' + key.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _value(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, str):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\t", "\\t")
        return f'"{escaped}"'
    if isinstance(value, list):
        return "[" + ", ".join(_value(item) for item in value) + "]"
    raise TypeError(f"unsupported TOML value {type(value).__name__}")


def _is_table_array(value) -> bool:
    return isinstance(value, list) and value != [] and all(isinstance(item, dict) for item in value)


def dumps(document: dict) -> str:
    lines: list[str] = []

    def emit_table(path: list[str], table: dict, header: str | None) -> None:
        scalars = [(k, v) for k, v in table.items() if not isinstance(v, dict) and not _is_table_array(v)]
        if header is not None:
            if lines:
                lines.append("")
            lines.append(header)
        for key, value in scalars:
            lines.append(f"{_key(key)} = {_value(value)}")
        for key, value in table.items():
            if isinstance(value, dict):
                sub = path + [key]
                emit_table(sub, value, "[" + ".".join(_key(p) for p in sub) + "]")
            elif _is_table_array(value):
                sub = path + [key]
                for item in value:
                    emit_table(sub, item, "[[" + ".".join(_key(p) for p in sub) + "]]")

    emit_table([], document, None)
    return "\n".join(lines) + "\n"
