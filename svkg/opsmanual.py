"""INZ Operational Manual (Immigration Instructions): student instructions U1-U16.

Product-owner decision, 23 Nov 2026 (IF-25): load the Operational Manual as the
authoritative Immigration Instructions source for the Student Visa, superseding the
build plan's original "do not use the Operational Manual" ground rule.

Scope (IF-29):
  * every page under "Student Visas" (U1-U16), current and historic versions;
  * every general section a *current* student page links to (e.g. A4, A5, E6.5, H5),
    current version only, plus the current pages listed in a cited section index.

`svkg fetch-opsmanual` crawls and caches raw HTML under data/opsmanual/pages/ and
writes data/opsmanual/instructions.json. The extractor reads that file; it never
fetches. Text is the published wording only (whitespace normalised, labels kept).
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from collections import deque
from datetime import date, datetime, timezone

import requests
from bs4 import BeautifulSoup, NavigableString, Tag

from .docspec import ROOT
from .legislation import clean

BASE = "https://www.immigration.govt.nz/opsmanual/"
ROOT_PAGE = "66958.htm"            # "Student Visas"
CACHE = ROOT / "data" / "opsmanual"
PAGES = CACHE / "pages"
MANIFEST = CACHE / "manifest.json"
PARSED = CACHE / "instructions.json"
UA = "Mozilla/5.0 (compatible; svkg-poc/0.2; synthetic POC)"
SOURCE_ID = "NZ-INZ-IMMIGRATION-INSTRUCTIONS"

CODE_RE = re.compile(r"^([A-Z]\d+(?:\.\d+)*)\b")
DATED_RE = re.compile(r"\((\d{2})/(\d{2})/(\d{4})\)\s*$")
EXPIRED_RE = re.compile(r"\(to ([^)]+)\)\s*$")
LIST_LABELS = {"listletter": "alpha", "listroman": "roman", "listnumber": "number", "listalpha2": "ALPHA"}


# --------------------------------------------------------------------------- fetch
def _get(page: str, session: requests.Session, manifest: dict, kind: str) -> str:
    fn = PAGES / page
    if fn.exists():
        return fn.read_text(encoding="utf-8", errors="ignore")
    r = session.get(BASE + page, timeout=30)
    r.raise_for_status()
    html = r.content.decode("utf-8", "ignore")
    PAGES.mkdir(parents=True, exist_ok=True)
    fn.write_text(html, encoding="utf-8")
    manifest[page] = {"url": BASE + "#" + page, "page_url": BASE + page, "kind": kind,
                      "retrieved_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
                      "sha256": hashlib.sha256(html.encode()).hexdigest()}
    time.sleep(0.25)
    return html


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


def page_title(soup: BeautifulSoup) -> str:
    t = soup.find("title")
    return clean(t.get_text()) if t else ""


def related(soup: BeautifulSoup) -> list[str]:
    out = []
    for p in soup.select("p.relateditem a[href]"):
        if re.fullmatch(r"\d+\.htm", p["href"]):
            out.append(p["href"])
    return out


def body_links(soup: BeautifulSoup) -> list[tuple[str, str]]:
    main = soup.find(id="main")
    if not main:
        return []
    out = []
    for a in main.find_all("a", href=True):
        if a.find_parent("table", class_="relatedtopics"):
            continue
        if re.fullmatch(r"\d+\.htm", a["href"]):
            out.append((a["href"], clean(a.get("title") or a.get_text())))
    return out


def fetch(download: bool = True) -> dict:
    manifest = json.loads(MANIFEST.read_text()) if MANIFEST.exists() else {}
    s = requests.Session()
    s.headers["User-Agent"] = UA
    if not download:
        s = None
    get = (lambda p, k: _get(p, s, manifest, k)) if download else \
        (lambda p, k: (PAGES / p).read_text(encoding="utf-8", errors="ignore"))
    # 1. student section, every version
    seen, q = set(), deque([ROOT_PAGE])
    while q:
        p = q.popleft()
        if p in seen:
            continue
        soup = _soup(get(p, "STUDENT"))
        title = page_title(soup)
        if p != ROOT_PAGE and not title.startswith("U"):
            continue
        seen.add(p)
        q.extend(c for c in related(soup) if c not in seen)
    student = sorted(seen)
    # 2. general sections cited by current student pages (current version, one level)
    refs = {}
    for p in student:
        soup = _soup(get(p, "STUDENT"))
        if DATED_RE.search(page_title(soup)):
            continue
        for href, title in body_links(soup):
            if not title.startswith("U") and href not in seen:
                refs[href] = title
    for href in sorted(refs):
        soup = _soup(get(href, "REFERENCE"))
        seen.add(href)
        if _is_index(soup):
            for c in related(soup):
                csoup = _soup(get(c, "REFERENCE"))
                t = page_title(csoup)
                if c not in seen and not DATED_RE.search(t) and CODE_RE.match(t) and \
                        CODE_RE.match(t).group(1).startswith(CODE_RE.match(page_title(soup)).group(1)):
                    seen.add(c)
    if download:
        MANIFEST.write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n")
    data = parse_all(sorted(seen), manifest)
    PARSED.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n")
    return {"pages": len(seen), "student_pages": len(student), "reference_pages": len(seen) - len(student),
            "instructions": len(data["pages"]), "cache": str(PARSED.relative_to(ROOT))}


def _is_index(soup) -> bool:
    main = soup.find(id="main")
    return bool(main and not main.find(["ol", "ul"]) and not main.select("p.bodytext") and related(soup))


# --------------------------------------------------------------------------- parse
def _roman(n: int) -> str:
    vals = [(10, "x"), (9, "ix"), (5, "v"), (4, "iv"), (1, "i")]
    out = ""
    for v, s in vals:
        while n >= v:
            out, n = out + s, n - v
    return out


def _label(kind: str, i: int) -> str:
    return {"alpha": "abcdefghijklmnopqrstuvwxyz"[i] if i < 26 else f"a{i}", "roman": _roman(i + 1),
            "number": str(i + 1), "ALPHA": "ABCDEFGHIJKLMNOPQRSTUVWXYZ"[i]}.get(kind, f"b{i + 1}")


def _own_text(li: Tag) -> str:
    parts = []
    for ch in li.children:
        if isinstance(ch, Tag) and ch.name in ("ol", "ul"):
            continue
        parts.append(ch.get_text(" ") if isinstance(ch, Tag) else str(ch))
    return clean(" ".join(parts))


def _links(el: Tag) -> list[str]:
    out = []
    for a in el.find_all("a", href=True):
        t = clean(a.get("title") or a.get_text())
        m = CODE_RE.match(t)
        if m:
            out.append(m.group(1))
    return out


def _list_node(ol: Tag) -> list[dict]:
    cls = (ol.get("class") or [""])[0]
    kind = LIST_LABELS.get(cls, "bullet")
    items = []
    for i, li in enumerate([c for c in ol.children if isinstance(c, Tag) and c.name == "li"]):
        node = {"label": _label(kind, i), "text": _own_text(li), "children": [],
                "links": _links(_strip_nested(li))}
        for sub in li.find_all(["ol", "ul"], recursive=False):
            node["children"] += _list_node(sub)
        items.append(node)
    return items


def _strip_nested(li: Tag) -> Tag:
    c = _soup(str(li))
    for sub in c.find_all(["ol", "ul"]):
        sub.decompose()
    return c


def _date(d, m, y) -> str:
    return f"{int(y):04d}-{int(m):02d}-{int(d):02d}"


def parse_page(page: str, html: str) -> dict | None:
    soup = _soup(html)
    main = soup.find(id="main")
    title = page_title(soup)
    m = CODE_RE.match(title)
    if not main or not m:
        return None
    code = m.group(1)
    dated = DATED_RE.search(title)
    expired = EXPIRED_RE.search(title)
    eff_from = None
    for eff in main.select("p.effectivedate"):
        d = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})", eff.get_text())
        if d:
            eff_from = _date(*d.groups())
            break
    if dated and not eff_from:
        eff_from = _date(*dated.groups())
    heading = clean(DATED_RE.sub("", EXPIRED_RE.sub("", title[len(code):])))
    units = [{"code": code, "heading": heading, "blocks": [], "see_also": []}]
    counters: dict[str, int] = {}
    def inside(el, names):
        for a in el.parents:
            if a is main:
                return False
            if a.name in names:
                return True
        return False

    for el in main.find_all(recursive=True):
        if not isinstance(el, Tag) or el.find_parent("table", class_="relatedtopics"):
            continue
        if inside(el, ("ol", "ul")) or (inside(el, ("table",)) and not el.name == "table"):
            continue
        cls = (el.get("class") or [""])[0]
        u = units[-1]
        if el.name == "p" and cls == "headingopsmanual":
            t = clean(el.get_text(" "))
            mm = CODE_RE.match(t)
            if mm:
                units.append({"code": mm.group(1), "heading": clean(t[len(mm.group(1)):]), "blocks": [],
                              "see_also": []})
                counters = {}
            else:
                u["blocks"].append({"label": _seq(counters, "h"), "text": t, "children": [], "links": []})
        elif el.name == "p" and cls == "reference":
            u["see_also"].append(clean(el.get_text(" ")))
        elif el.name in ("ol", "ul"):
            nodes = _list_node(el)
            last = u["blocks"][-1] if u["blocks"] else None
            if last and not last["children"] and last["label"].startswith(("p", "note")) and \
                    last["text"].rstrip().endswith((":", "\u2014", "-")):
                last["children"] = nodes          # list introduced by a lead-in paragraph
            else:
                u["blocks"] += nodes
        elif el.name == "p" and cls in ("bodytext", "note", "listnote", "listnote1", "listcontinue"):
            t = clean(el.get_text(" "))
            if t:
                pref = "note" if "note" in cls else "p"
                u["blocks"].append({"label": _seq(counters, pref), "text": t, "children": [],
                                    "links": _links(el)})
        elif el.name == "table" and "tableintopic" in (el.get("class") or []) and not inside(el, ("table",)):
            rows = [" | ".join(clean(td.get_text(" ")) for td in tr.find_all(["td", "th"]))
                    for tr in el.find_all("tr")]
            u["blocks"].append({"label": _seq(counters, "table"), "text": " ; ".join(r for r in rows if r.strip(" |")),
                                "children": [], "links": _links(el)})
    return {"page": page, "code": code, "title": heading, "page_title": title,
            "version_kind": "HISTORIC" if dated else ("EXPIRED" if expired else "CURRENT"),
            "effective_from": eff_from, "expired_note": expired.group(1) if expired else None,
            "is_index": _is_index(soup), "children_pages": related(soup), "units": units,
            "section_listing": [clean(a.get_text(" ")) for a in soup.select("p.relateditem a[href]")]}


def _seq(counters, prefix):
    counters[prefix] = counters.get(prefix, 0) + 1
    return f"{prefix}{counters[prefix]}"


def parse_all(pages: list[str], manifest: dict) -> dict:
    out = []
    for p in pages:
        rec = parse_page(p, (PAGES / p).read_text(encoding="utf-8", errors="ignore"))
        if rec:
            m = manifest.get(p, {})
            rec.update(url=BASE + "#" + p, kind=m.get("kind"), retrieved_at=m.get("retrieved_at"),
                       sha256=m.get("sha256"))
            out.append(rec)
    # version chains: group by code; newest effective first; effective_to = next newer's from
    by_code: dict[str, list[dict]] = {}
    for r in out:
        by_code.setdefault(r["code"], []).append(r)
    for code, recs in by_code.items():
        recs.sort(key=lambda r: (r["version_kind"] != "CURRENT" and r["version_kind"] != "EXPIRED",
                                 r["effective_from"] or ""), reverse=False)
        cur = [r for r in recs if r["version_kind"] in ("CURRENT", "EXPIRED")]
        hist = sorted([r for r in recs if r["version_kind"] == "HISTORIC"], key=lambda r: r["effective_from"] or "",
                      reverse=True)
        chain = cur[:1] + hist
        for newer, older in zip(chain, chain[1:]):
            older["effective_to"] = newer.get("effective_from")
            older["superseded_by_page"] = newer["page"]
        for r in recs:
            r.setdefault("effective_to", None)
    return {"source_id": SOURCE_ID, "base_url": BASE, "root_page": ROOT_PAGE,
            "parsed_on": date.today().isoformat(), "pages": out}


def load() -> dict | None:
    return json.loads(PARSED.read_text()) if PARSED.exists() else None


# --------------------------------------------------------------------------- flatten to provisions/rules
def walk(nodes, path=()):
    for n in nodes:
        p = path + (n["label"],)
        yield p, n
        yield from walk(n["children"], p)


def render(nodes) -> str:
    parts = []
    for n in nodes:
        parts.append(f"({n['label']}) {n['text']}" if not n["label"][0].isalpha() or len(n["label"]) <= 4
                     and not n["label"].startswith(("p", "note", "table", "h")) else n["text"])
        if n["children"]:
            parts.append(render(n["children"]))
    return clean(" ".join(parts))


def node_text(n) -> str:
    return render([n])
