"""Phase 2: exact provision wording from legislation.govt.nz (decision 8).

`svkg fetch-legislation` downloads each instrument's current version, caches the
raw HTML under data/legislation/, and writes data/legislation/provision-text.json
keyed by provision_id. The extractor reads that cache; it never fetches.

Text is the published wording only: labels such as "(1)" and "(a)" are kept,
whitespace is normalised and zero-width characters are removed. History notes and
"Compare" notes are excluded. Nothing is paraphrased: a provision that cannot be
located stays SOURCE_TEXT_NOT_RETRIEVED with a reason.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup, Tag

from .docspec import ROOT

CACHE_DIR = ROOT / "data" / "legislation"
TEXT_CACHE = CACHE_DIR / "provision-text.json"
BASE = "https://www.legislation.govt.nz/{path}/en/latest/"
INSTRUMENTS = {
    "NZ-IMM-ACT-2009": "act/public/2009/51",
    "NZ-IMM-VEPRM-REGS-2010": "secondary-legislation/pco-drafted/2010/241",
    "NZ-IMM-CIO-REGS-2010": "secondary-legislation/pco-drafted/2010/238",
    "NZ-PRIVACY-ACT-2020": "act/public/2020/31",
}
NOT_PUBLISHED = {
    "NZ-EDU-PASTORAL-CODE-2021": "Not published on legislation.govt.nz (decision 8: SOURCE_TEXT_NOT_RETRIEVED)",
}
UA = "Mozilla/5.0 (compatible; svkg-poc/0.2; synthetic POC)"
MONTHS = {m: i for i, m in enumerate(["January", "February", "March", "April", "May", "June", "July",
                                       "August", "September", "October", "November", "December"], 1)}


def clean(text: str) -> str:
    """Normalise whitespace only; the words and punctuation are left exactly as published."""
    t = " ".join(text.replace("\ufeff", "").replace("\u200b", "").split())
    t = re.sub(r"\s+([,.;:)\]\u2019\u201d])", r"\1", t)   # space added before punctuation by link markup
    return re.sub(r"([(\[\u2018\u201c])\s+", r"\1", t)


def version_date(html: str) -> tuple[str | None, str | None]:
    m = re.search(r'class="reprint-date"[^>]*>([^<]*)<', html)
    if not m:
        return None, None
    raw = clean(m.group(1))
    d = re.search(r"(?:Version|Reprint) as at (\d{1,2}) ([A-Z][a-z]+) (\d{4})", raw)
    if not d:
        return None, raw
    return f"{int(d.group(3)):04d}-{MONTHS[d.group(2)]:02d}-{int(d.group(1)):02d}", raw


def _label(el: Tag, selector: str) -> str | None:
    lab = el.select_one(selector)
    return lab.get_text(strip=True) if lab else None


def top_units(soup: BeautifulSoup) -> dict[str, Tag]:
    """Body provisions by number. Schedule provisions with the same label are excluded."""
    out = {}
    for p in soup.select("div.prov"):
        num = _label(p, ":scope > h5.prov > span.label")
        if not num:
            continue
        classes = [c for a in p.parents if isinstance(a, Tag) for c in (a.get("class") or [])]
        if "schedule" in classes or "body" not in classes:
            continue
        out.setdefault(num, p)
    return out


def _render(node: Tag) -> str:
    """Published text of a node, keeping labels, excluding history and compare notes."""
    node = BeautifulSoup(str(node), "html.parser")
    for junk in node.select("p.cf, div.history, p.history-note, div.history-note, span.history"):
        junk.decompose()
    return clean(node.get_text(" "))


def _children(node: Tag, cls: str, label_sel: str) -> dict[str, Tag]:
    """Direct structural children (not those nested inside another of the same kind)."""
    out = {}
    for c in node.find_all("div", class_=cls):
        anc = c.parent
        nested = False
        while anc is not None and anc is not node:
            if isinstance(anc, Tag) and cls in (anc.get("class") or []):
                nested = True
                break
            anc = anc.parent
        if nested:
            continue
        lab = _label(c, label_sel)
        if lab:
            out.setdefault(lab.strip("()"), c)
    return out


def locate(prov: Tag, path: tuple[str, ...]) -> Tag | None:
    body = prov.select_one(":scope > div.prov-body")
    if body is None:
        return None
    if not path:
        return body
    subs = _children(body, "subprov", ":scope > p.subprov > span.label")
    cur = subs.get(path[0])
    for part in path[1:]:
        if cur is None:
            return None
        cur = _children(cur, "label-para", ":scope > h5.label-para > span.label").get(part)
    return cur


def ipp_texts(s22: Tag) -> dict[str, str]:
    """Privacy Act s 22 table: 'Information privacy principle N' starts each principle."""
    out, cur, buf = {}, None, []
    for tr in s22.select("tr"):
        t = clean(tr.get_text(" "))
        m = re.fullmatch(r"Information privacy principle (\w+)", t)
        if m:
            if cur:
                out[cur] = " ".join(buf)
            cur, buf = m.group(1), [t]
        elif cur and t:
            buf.append(_render(tr))
    if cur:
        out[cur] = " ".join(buf)
    return out


_PATH_RE = re.compile(r"\(([0-9A-Za-z]+)\)")


def parse_reference(p: dict) -> tuple[str, tuple[str, ...], str | None]:
    ref = p["reference"]
    m = re.match(r"(?:s|r|cl) ([0-9A-Z]+)(.*)$", ref)
    num, rest = m.group(1), m.group(2)
    ipp = re.search(r"IPP (\w+)", rest)
    return num, tuple(_PATH_RE.findall(rest)) if not ipp else (), ipp.group(1) if ipp else None


def extract_texts(provisions: list[dict], retrieved_at: str) -> dict[str, dict]:
    out: dict[str, dict] = {}
    by_source: dict[str, list[dict]] = {}
    for p in provisions:
        by_source.setdefault(p["source_id"], []).append(p)
    for sid, provs in by_source.items():
        if sid in NOT_PUBLISHED:
            for p in provs:
                out[p["provision_id"]] = {"source_text_status": "SOURCE_TEXT_NOT_RETRIEVED",
                                          "reason": NOT_PUBLISHED[sid]}
            continue
        f = CACHE_DIR / f"{sid}.html"
        if sid not in INSTRUMENTS or not f.exists():
            for p in provs:
                out[p["provision_id"]] = {"source_text_status": "SOURCE_TEXT_NOT_RETRIEVED",
                                          "reason": "Instrument page not cached"}
            continue
        html = f.read_text(encoding="utf-8")
        version, version_raw = version_date(html)
        sha = hashlib.sha256(html.encode("utf-8")).hexdigest()
        base = BASE.format(path=INSTRUMENTS[sid])
        units = top_units(BeautifulSoup(html, "html.parser"))
        ipps = None
        for p in provs:
            num, path, ipp = parse_reference(p)
            prov = units.get(num)
            rec = {"source_version": version, "version_as_published": version_raw,
                   "retrieved_at": retrieved_at, "html_sha256": sha, "html_cache": f"data/legislation/{f.name}"}
            if prov is None:
                out[p["provision_id"]] = {**rec, "source_text_status": "SOURCE_TEXT_NOT_RETRIEVED",
                                          "reason": f"{p['reference']} not found in the body of the instrument"}
                continue
            heading_el = prov.select_one(":scope > h5.prov")
            heading = clean(heading_el.get_text(" ")).removeprefix(num).strip() if heading_el else None
            url = f"{base}#{prov.get('id')}" if prov.get("id") else base
            if ipp:
                ipps = ipps if ipps is not None else ipp_texts(prov)
                text = ipps.get(ipp)
            else:
                node = locate(prov, path)
                text = _render(node) if node is not None else None
            if not text:
                out[p["provision_id"]] = {**rec, "source_text_status": "SOURCE_TEXT_NOT_RETRIEVED",
                                          "source_url": url, "heading": heading,
                                          "reason": f"{p['reference']} sub-unit not located on the page"}
                continue
            out[p["provision_id"]] = {**rec, "source_text_status": "RETRIEVED", "source_text": text,
                                      "heading": heading, "source_url": url}
    return out


def fetch(download: bool = True) -> dict:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    fetched = {}
    if download:
        for sid, path in INSTRUMENTS.items():
            r = requests.get(BASE.format(path=path), headers={"User-Agent": UA}, timeout=60)
            r.raise_for_status()
            (CACHE_DIR / f"{sid}.html").write_text(r.text, encoding="utf-8")
            fetched[sid] = {"url": r.url, "bytes": len(r.content)}
    provisions = json.loads((ROOT / "regulatory/provisions.json").read_text())["provisions"]
    retrieved_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if not download and TEXT_CACHE.exists():
        retrieved_at = json.loads(TEXT_CACHE.read_text()).get("retrieved_at", retrieved_at)
    provisions = [p for p in provisions if p["source_id"] in INSTRUMENTS or p["source_id"] in NOT_PUBLISHED]
    texts = extract_texts(provisions, retrieved_at)
    versions = {}
    for sid in INSTRUMENTS:
        f = CACHE_DIR / f"{sid}.html"
        if f.exists():
            v, raw = version_date(f.read_text(encoding="utf-8"))
            versions[sid] = {"version_id": v, "version_as_published": raw, "url": BASE.format(path=INSTRUMENTS[sid])}
    TEXT_CACHE.write_text(json.dumps({"retrieved_at": retrieved_at, "instruments": versions,
                                      "provisions": texts}, indent=2, ensure_ascii=False) + "\n")
    status = {}
    for v in texts.values():
        status[v["source_text_status"]] = status.get(v["source_text_status"], 0) + 1
    return {"downloaded": fetched, "instrument_versions": versions, "provision_status": status,
            "not_retrieved": sorted(k for k, v in texts.items() if v["source_text_status"] != "RETRIEVED"
                                    and not k.startswith("NZ-EDU-PASTORAL-CODE"))}


def load_cache() -> dict | None:
    return json.loads(TEXT_CACHE.read_text()) if TEXT_CACHE.exists() else None
