"""Parse the provision references used in the spec into addressable provision IDs.

ID scheme follows Appendix F.2: NZ-IMM-ACT-2009-S58-3-A (source, unit, then each
bracketed level upper-cased), parent NZ-IMM-ACT-2009-S58-3.

Handles every form that appears in the document, e.g.
  s 57(1) | s 60(1),(3) | s 15(1)(d)-(f), s 17 | s 16, s 17 | r 20(1)(a), (2)
  r 23AA(4); r 23A(3) | cls 53(1)(b), 54 | s 22, IPP 3A | ss 114-115 | cls 40-44
"""
from __future__ import annotations

import re
from dataclasses import dataclass

UNIT_CODE = {"s": "S", "r": "R", "cl": "CL"}
UNIT_TYPE = {"s": "Section", "r": "Regulation", "cl": "Clause"}
UNIT_WORD = {"s": "s", "r": "r", "cl": "cl"}
# Below the top unit the document only names Subsection and Paragraph (F.2); the
# same depth names are reused for regulations and clauses (interpretation flag IF-07).
DEPTH_TYPE = {1: "Subsection", 2: "Paragraph", 3: "Subparagraph"}


@dataclass(frozen=True)
class ProvRef:
    source_id: str
    unit: str                 # s | r | cl
    number: str               # "58", "23AA"
    path: tuple[str, ...]     # ("3", "a")
    ipp: str | None = None    # Privacy Act information privacy principle under s 22

    @property
    def provision_id(self) -> str:
        parts = [self.source_id, f"{UNIT_CODE[self.unit]}{self.number}", *[p.upper() for p in self.path]]
        if self.ipp:
            parts.append(f"IPP{self.ipp}")
        return "-".join(parts)

    @property
    def reference(self) -> str:
        if self.ipp:
            return f"{UNIT_WORD[self.unit]} {self.number}, IPP {self.ipp}"
        return f"{UNIT_WORD[self.unit]} {self.number}" + "".join(f"({p})" for p in self.path)

    @property
    def provision_type(self) -> str:
        if self.ipp:
            return "Paragraph"   # IF-07: IPPs modelled as sub-units of s 22
        return DEPTH_TYPE[len(self.path)] if self.path else UNIT_TYPE[self.unit]

    def parent(self) -> "ProvRef | None":
        if self.ipp:
            return ProvRef(self.source_id, self.unit, self.number, self.path)
        if not self.path:
            return None
        return ProvRef(self.source_id, self.unit, self.number, self.path[:-1])

    def lineage(self) -> list["ProvRef"]:
        out, cur = [], self
        while cur is not None:
            out.append(cur)
            cur = cur.parent()
        return list(reversed(out))


_UNIT_RE = re.compile(r"^(ss|s|r|cls|cl)\s+(.*)$")
_NUM_RE = re.compile(r"^(\d+[A-Z]*)((?:\([0-9a-z]+\))*)(?:-\(([0-9a-z]+)\))?$")
_RANGE_RE = re.compile(r"^(\d+)-(\d+)$")
_BRACKETS_RE = re.compile(r"\(([0-9a-z]+)\)")
_LETTER_SEQ = "abcdefghijklmnopqrstuvwxyz"


def _expand_last(path: tuple[str, ...], end: str) -> list[tuple[str, ...]]:
    start = path[-1]
    if start in _LETTER_SEQ and end in _LETTER_SEQ:
        seq = _LETTER_SEQ[_LETTER_SEQ.index(start): _LETTER_SEQ.index(end) + 1]
        return [path[:-1] + (c,) for c in seq]
    if start.isdigit() and end.isdigit():
        return [path[:-1] + (str(i),) for i in range(int(start), int(end) + 1)]
    raise ValueError(f"cannot expand range {path}-{end}")


def parse(reference: str, source_id: str) -> list[ProvRef]:
    """Return every provision a reference string cites, in order."""
    out: list[ProvRef] = []
    unit = number = None
    for raw in re.split(r"[;,]", reference):
        tok = raw.strip()
        if not tok:
            continue
        if tok.startswith("IPP"):
            body = tok[3:].lstrip("s").strip()
            m = _RANGE_RE.match(body)
            ipps = [str(i) for i in range(int(m.group(1)), int(m.group(2)) + 1)] if m else [body]
            if unit is None:
                raise ValueError(f"IPP without section in {reference!r}")
            out += [ProvRef(source_id, unit, number, (), ipp=i) for i in ipps]
            continue
        m = _UNIT_RE.match(tok)
        if m:
            u = m.group(1)
            unit = {"ss": "s", "cls": "cl"}.get(u, u)
            tok = m.group(2).strip()
        if unit is None:
            raise ValueError(f"no unit in {reference!r}")
        if tok.startswith("("):                       # bare "(3)": sibling subsection
            path = tuple(_BRACKETS_RE.findall(tok))
            out.append(ProvRef(source_id, unit, number, path))
            continue
        m = _RANGE_RE.match(tok)
        if m:                                          # "114-115", "40-44"
            for i in range(int(m.group(1)), int(m.group(2)) + 1):
                out.append(ProvRef(source_id, unit, str(i), ()))
            number = m.group(2)
            continue
        m = _NUM_RE.match(tok.replace(" ", ""))
        if not m:
            raise ValueError(f"unparsed token {tok!r} in {reference!r}")
        number = m.group(1)
        path = tuple(_BRACKETS_RE.findall(m.group(2) or ""))
        if m.group(3):
            out += [ProvRef(source_id, unit, number, p) for p in _expand_last(path, m.group(3))]
        else:
            out.append(ProvRef(source_id, unit, number, path))
    return out
