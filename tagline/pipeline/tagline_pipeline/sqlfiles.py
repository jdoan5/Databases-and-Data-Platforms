"""SQL files: `{{ name }}` substitution and the documentation header each model carries.

Deliberately tiny instead of Jinja: the only templating the models need is dataset
names and the optional site-export block, and a reader should be able to paste a
rendered model into the BigQuery console and run it.

A model file starts with its documentation, which the runner applies to the table
after it is built (so it shows in the BigQuery console):

    -- @table One row per ...
    -- @column source: Which export the row came from.
    -- @column items.item_id: Nested fields use a dotted path.
    --     A line indented by four or more spaces after `--` continues the previous entry.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

_VAR = re.compile(r"\{\{\s*([a-z_][a-z0-9_]*)\s*\}\}")
_ANY_TEMPLATE = re.compile(r"\{\{|\}\}|\{%|%\}")
_DOC_LINE = re.compile(r"^--\s@(table|column|check)\b\s?(.*)$")
_CONTINUATION = re.compile(r"^--\s{4,}(\S.*)$")


class TemplateError(ValueError):
    pass


def render(sql: str, context: dict[str, object]) -> str:
    """Replace every {{ name }}; a name missing from the context is an error, not blank."""
    missing = sorted({m.group(1) for m in _VAR.finditer(sql)} - context.keys())
    if missing:
        raise TemplateError(f"no value for {', '.join(missing)}")
    out = _VAR.sub(lambda m: str(context[m.group(1)]), sql)
    leftover = _ANY_TEMPLATE.search(_VAR.sub("", sql))
    if leftover:
        raise TemplateError(f"unrecognised template syntax near {sql[max(leftover.start() - 20, 0):leftover.end() + 20]!r}")
    return out


@dataclass
class Doc:
    table: str = ""
    columns: dict[str, str] = field(default_factory=dict)
    check: str = ""


def parse_doc(sql: str) -> Doc:
    """Read the @table / @column / @check header (only the leading comment block)."""
    doc = Doc()
    last: tuple[str, str] | None = None  # (kind, column name) of the entry a continuation extends
    for line in sql.splitlines():
        stripped = line.rstrip()
        if not stripped.startswith("--"):
            if stripped:
                break  # header ends at the first line of SQL
            continue
        m = _DOC_LINE.match(stripped)
        if m:
            kind, text = m.group(1), m.group(2).strip()
            if kind == "column":
                name, sep, desc = text.partition(":")
                if not sep or not name.strip() or not desc.strip():
                    raise TemplateError(f"bad @column line: {stripped!r}")
                doc.columns[name.strip()] = desc.strip()
                last = ("column", name.strip())
            elif kind == "table":
                doc.table = text
                last = ("table", "")
            else:
                doc.check = text
                last = ("check", "")
            continue
        c = _CONTINUATION.match(stripped)
        if c and last is not None:
            kind, name = last
            if kind == "column":
                doc.columns[name] += " " + c.group(1).strip()
            elif kind == "table":
                doc.table += " " + c.group(1).strip()
            else:
                doc.check += " " + c.group(1).strip()
        else:
            last = None
    return doc


@dataclass(frozen=True)
class Model:
    """A model file: sql/models/NN_<table>.sql. stg_/int_ build into staging, fct_/mart_ into marts."""

    path: Path

    @property
    def name(self) -> str:
        return re.sub(r"^\d+_", "", self.path.stem)

    @property
    def layer(self) -> str:
        if self.name.startswith(("stg_", "int_")):
            return "staging"
        if self.name.startswith(("fct_", "mart_")):
            return "marts"
        raise TemplateError(f"{self.path.name}: model names start with stg_, int_, fct_ or mart_")

    def sql(self) -> str:
        return self.path.read_text(encoding="utf-8")


def list_models(sql_dir: Path) -> list[Model]:
    """Models in build order (the numeric prefix)."""
    return [Model(p) for p in sorted((sql_dir / "models").glob("[0-9]*_*.sql"))]


def list_sql(directory: Path) -> list[Path]:
    return sorted(directory.glob("*.sql"))
