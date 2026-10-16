"""Minimal low-level XLSX writer for M6 importer tests.

openpyxl cannot save a formula together with a cached result, and the importer framework must
tell literal cells, cached formula values, missing caches and Excel errors apart. This writer
emits the OOXML parts directly so tests can generate such workbooks at runtime. All content is
synthetic; no binary fixture is committed.

Cell values:
    None                → no cell
    str / int / float / bool / date / datetime → literal
    E("#REF!")          → literal Excel error
    F("A1*2", cached)   → formula; cached may be a value, E(...), or MISSING (no saved result)
"""
from __future__ import annotations

import datetime as dt
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence
from xml.sax.saxutils import escape

MISSING = object()
_EPOCH = dt.datetime(1899, 12, 30)


@dataclass(frozen=True)
class E:
    code: str


@dataclass(frozen=True)
class F:
    formula: str
    cached: Any = MISSING


def col_letter(n: int) -> str:
    s = ""
    while n:
        n, rem = divmod(n - 1, 26)
        s = chr(65 + rem) + s
    return s


def _serial(v: dt.date | dt.datetime) -> str:
    if isinstance(v, dt.datetime):
        delta = v - _EPOCH
    else:
        delta = dt.datetime(v.year, v.month, v.day) - _EPOCH
    return repr(delta.days + delta.seconds / 86400) if delta.seconds else str(delta.days)


def _value_xml(ref: str, v: Any, formula: str | None = None) -> str:
    f = f"<f>{escape(formula)}</f>" if formula is not None else ""
    if v is MISSING:
        return f'<c r="{ref}">{f}</c>'
    if isinstance(v, E):
        return f'<c r="{ref}" t="e">{f}<v>{escape(v.code)}</v></c>'
    if isinstance(v, bool):
        return f'<c r="{ref}" t="b">{f}<v>{int(v)}</v></c>'
    if isinstance(v, (int, float)):
        return f'<c r="{ref}">{f}<v>{v!r}</v></c>'
    if isinstance(v, dt.datetime):
        return f'<c r="{ref}" s="2">{f}<v>{_serial(v)}</v></c>'
    if isinstance(v, dt.date):
        return f'<c r="{ref}" s="1">{f}<v>{_serial(v)}</v></c>'
    if isinstance(v, str):
        if formula is not None:
            return f'<c r="{ref}" t="str">{f}<v>{escape(v)}</v></c>'
        return f'<c r="{ref}" t="inlineStr"><is><t xml:space="preserve">{escape(v)}</t></is></c>'
    raise TypeError(type(v))


def _cell_xml(ref: str, v: Any) -> str:
    if isinstance(v, F):
        return _value_xml(ref, v.cached, v.formula)
    return _value_xml(ref, v)


def _sheet_xml(rows: Sequence[Sequence[Any]]) -> str:
    out, max_c = [], 1
    for r, row in enumerate(rows, start=1):
        cells = [_cell_xml(f"{col_letter(c)}{r}", v) for c, v in enumerate(row, start=1) if v is not None]
        if row:
            max_c = max(max_c, len(row))
        if cells:
            out.append(f'<row r="{r}">{"".join(cells)}</row>')
    dim = f"A1:{col_letter(max_c)}{max(len(rows), 1)}"
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            f'<dimension ref="{dim}"/><sheetData>{"".join(out)}</sheetData></worksheet>')


_STYLES = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
           '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
           '<fonts count="1"><font><sz val="11"/><name val="Calibri"/></font></fonts>'
           '<fills count="2"><fill><patternFill patternType="none"/></fill>'
           '<fill><patternFill patternType="gray125"/></fill></fills>'
           '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
           '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
           '<cellXfs count="3"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
           '<xf numFmtId="14" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>'
           '<xf numFmtId="22" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/></cellXfs>'
           '</styleSheet>')

_NS_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def write_xlsx(path: str | Path, sheets: dict[str, Sequence[Sequence[Any]]], *,
               external_links: Sequence[str] = ()) -> Path:
    """Write a workbook with the given sheets (name → rows). `external_links` are link targets
    (e.g. a fictional 'file:///C:/Users/someone/Other.xlsx'); the parts are only declared."""
    path = Path(path)
    names = list(sheets)
    ct = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
          '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
          '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
          '<Default Extension="xml" ContentType="application/xml"/>'
          '<Override PartName="/xl/workbook.xml" '
          'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
          '<Override PartName="/xl/styles.xml" '
          'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>']
    rels = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">']
    sheet_tags = []
    for i, name in enumerate(names, start=1):
        ct.append(f'<Override PartName="/xl/worksheets/sheet{i}.xml" '
                  'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>')
        rels.append(f'<Relationship Id="rId{i}" Type="{_NS_REL}/worksheet" Target="worksheets/sheet{i}.xml"/>')
        sheet_tags.append(f'<sheet name="{escape(name)}" sheetId="{i}" r:id="rId{i}"/>')
    n = len(names)
    rels.append(f'<Relationship Id="rId{n + 1}" Type="{_NS_REL}/styles" Target="styles.xml"/>')
    ext_tags = []
    for j, _target in enumerate(external_links, start=1):
        rid = f"rId{n + 1 + j}"
        ct.append(f'<Override PartName="/xl/externalLinks/externalLink{j}.xml" '
                  'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.externalLink+xml"/>')
        rels.append(f'<Relationship Id="{rid}" Type="{_NS_REL}/externalLink" '
                    f'Target="externalLinks/externalLink{j}.xml"/>')
        ext_tags.append(f'<externalReference r:id="{rid}"/>')
    ct.append("</Types>")
    rels.append("</Relationships>")
    workbook = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                f'xmlns:r="{_NS_REL}"><sheets>{"".join(sheet_tags)}</sheets>'
                + (f'<externalReferences>{"".join(ext_tags)}</externalReferences>' if ext_tags else "")
                + "</workbook>")
    root_rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                 '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                 f'<Relationship Id="rId1" Type="{_NS_REL}/officeDocument" Target="xl/workbook.xml"/>'
                 '</Relationships>')
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", "".join(ct))
        z.writestr("_rels/.rels", root_rels)
        z.writestr("xl/workbook.xml", workbook)
        z.writestr("xl/_rels/workbook.xml.rels", "".join(rels))
        z.writestr("xl/styles.xml", _STYLES)
        for i, name in enumerate(names, start=1):
            z.writestr(f"xl/worksheets/sheet{i}.xml", _sheet_xml(sheets[name]))
        for j, target in enumerate(external_links, start=1):
            z.writestr(f"xl/externalLinks/externalLink{j}.xml",
                       '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                       '<externalLink xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                       f'xmlns:r="{_NS_REL}"><externalBook r:id="rId1"><sheetNames><sheetName val="S"/>'
                       '</sheetNames></externalBook></externalLink>')
            z.writestr(f"xl/externalLinks/_rels/externalLink{j}.xml.rels",
                       '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                       '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                       f'<Relationship Id="rId1" Type="{_NS_REL}/externalLinkPath" '
                       f'Target="{escape(target)}" TargetMode="External"/></Relationships>')
    return path
