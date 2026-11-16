#!/usr/bin/env python3
"""
mlr_search.py — відтворювана реалізація розділу 3.2 «Стратегія пошуку»
мультиголосого огляду літератури з платформної інженерії (PE) та IDP.

Охоплює
  3.2.1  пошук в академічних базах (основний рядок + 4 фасетні рядки),
         вікно дат 2020-01 … 2026-02, дедуплікацію, прапорці для скринінгу
         за назвами, прямий і зворотний «сніговий ком» від 10 найцитованіших
         включених джерел;
  3.2.2  реєстр сірої літератури (таблиця 1) з перевіркою власних
         критеріїв статті.

Свідомі відхилення від тексту статті (і чому)
  * Google Scholar не має API, а скрейпінг порушує його умови. Замість цього
    імпортується CSV з Publish or Perish (--scholar-csv).
  * ACM DL не має публічного пошукового API -> охоплюється через Crossref
    з фільтром префікса DOI 10.1145.
  * Кожна база розуміє булеву логіку по-своєму. Тому після вибірки той самий
    рядок застосовується ЛОКАЛЬНО до назви+анотації і звітується окремо:
    інакше «той самий запит» у п'яти базах — це п'ять різних запитів.
  * Автоматика нічого не виключає: вона лише ставить прапорець для людини.
  * Збіг двох проходів скринінгу рахується як Cohen's kappa, а не відсоток.
  * Рівень включення звітується з явними знаменниками — окремо для
    академічного потоку, «снігового кома» та сірої літератури.

Ключі API (необов'язкові; база без ключа пропускається з повідомленням):
  IEEE_API_KEY, SPRINGER_API_KEY, ELSEVIER_API_KEY, S2_API_KEY

Залежності: лише стандартна бібліотека Python 3.10+.

Приклади
  python mlr_search.py search    --out run1 --mailto you@example.org
  python mlr_search.py search    --out run1 --mailto you@example.org --scholar-csv pop.csv
  python mlr_search.py agreement --screening run1/screening.csv
  python mlr_search.py snowball  --screening run1/screening.csv --out run1 --mailto you@example.org
  python mlr_search.py report    --screening run1/screening.csv --snowball run1/snowball_screening.csv
  python mlr_search.py gray
  python mlr_search.py demo      --out demo        # офлайн-перевірка всього конвеєра
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import random
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path
from typing import Callable, Iterator

# ─────────────────────────────── протокол ────────────────────────────────

DATE_FROM = "2020-01-01"
DATE_TO = "2026-02-28"
YEAR_FROM, YEAR_TO = 2020, 2026


@dataclass(frozen=True)
class Query:
    """Запит = AND над групами, кожна група = OR над фразами."""
    name: str
    groups: tuple[tuple[str, ...], ...]


PE_TERMS = (
    "platform engineering", "internal developer portal",
    "internal developer platform", "internal development platform", "developer platform",
    "developer experience platform",
)
CONTEXT_TERMS = (
    "software engineering", "DevOps", "cloud", "microservices",
    "kubernetes", "software development", "developer productivity",
)
PE_CONTEXT = ("platform engineering", "internal developer platform",
              "developer portal", "developer platform")

# Основний рядок — дослівно зі статті. Фасетні рядки стаття лише перелічує
# темами; контекстні групи нижче — моя операціоналізація. Без них «Port» і
# «Cortex» повертають шум про порти й кору мозку.
QUERIES: list[Query] = [
    Query("primary", (PE_TERMS, CONTEXT_TERMS)),
    Query("tools", (("Backstage", "Port", "Cortex", "Humanitec"), PE_CONTEXT)),
    Query("concepts", (("golden path", "service catalog", "developer self-service"),
                       PE_CONTEXT + ("DevOps",))),
    # Фасет метрик без контекстної групи — саме так у статті. Він і є джерелом
    # розмивання корпусу літературою про DevEx без PE.
    Query("metrics", (("DORA metrics", "SPACE framework",
                       "developer experience measurement"),)),
    Query("organizational", (("platform team", "Team Topologies",
                              "cognitive load reduction"),
                             ("software", "DevOps", "developer"))),
]
QUERY_BY_NAME = {q.name: q for q in QUERIES}


def render_boolean(q: Query) -> str:
    """Синтаксис для OpenAlex, IEEE, Springer, Elsevier."""
    return " AND ".join("(" + " OR ".join(f'"{t}"' for t in g) + ")" for g in q.groups)


def render_s2(q: Query) -> str:
    """Semantic Scholar bulk: + = AND, | = OR."""
    return " + ".join("(" + " | ".join(f'"{t}"' for t in g) + ")" for g in q.groups)


def render_arxiv(q: Query) -> str:
    return " AND ".join("(" + " OR ".join(f'all:"{t}"' for t in g) + ")" for g in q.groups)


# ─────────────────────────── нормалізація тексту ─────────────────────────

def norm_text(s: str | None) -> str:
    s = unicodedata.normalize("NFKC", s or "").casefold()
    s = re.sub(r"[-\u2010\u2011\u2012\u2013\u2014_/]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def title_key(t: str | None) -> str:
    return re.sub(r"[^a-z0-9 ]", "", norm_text(t)).strip()


def norm_doi(d: str | None) -> str | None:
    if not d:
        return None
    d = d.strip().lower()
    d = re.sub(r"^https?://(dx\.)?doi\.org/", "", d)
    d = d.removeprefix("doi:").strip()
    return d or None


@lru_cache(maxsize=None)
def _pattern(term: str) -> re.Pattern:
    t = norm_text(term)
    if t.endswith("s") and not t.endswith("ss"):
        t = t[:-1]  # "microservices" має збігатися і з "microservice"
    # однина й множина: "platform team" = "platform teams"
    return re.compile(r"\b" + re.escape(t) + r"(?:s|es)?\b")


def matches(q: Query, text: str | None) -> bool:
    t = norm_text(text)
    return all(any(_pattern(term).search(t) for term in g) for g in q.groups)


def strip_tags(s: str | None) -> str | None:
    return re.sub(r"<[^>]+>", " ", s).strip() if s else s


# ─────────────────────────────── модель ──────────────────────────────────

@dataclass
class Record:
    title: str
    year: int | None
    doi: str | None
    authors: list[str]
    venue: str | None
    abstract: str | None
    url: str | None
    citations: int | None
    source_db: str
    query: str
    origin: str = "database"          # або snowball-forward:<seed>, snowball-backward:<seed>
    openalex_id: str | None = None


@dataclass
class Candidate:
    id: str
    title: str
    year: int | None
    doi: str | None
    venue: str | None
    abstract: str | None
    citations: int | None
    openalex_id: str | None
    source_dbs: set[str]
    queries: set[str]
    origins: set[str]
    n_raw: int


# ─────────────────────────────────── HTTP ────────────────────────────────

USER_AGENT = "mlr-pe-search/1.0"


def http_get(url: str, params: dict | None = None, headers: dict | None = None,
             retries: int = 4, pause: float = 1.5) -> str:
    if params:
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params, safe=":,*")
    h = {"User-Agent": USER_AGENT, **(headers or {})}
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=60) as r:
                return r.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and attempt < retries - 1:
                time.sleep(pause * 2 ** attempt)
                continue
            raise
        except urllib.error.URLError:
            if attempt < retries - 1:
                time.sleep(pause * 2 ** attempt)
                continue
            raise
    raise RuntimeError("unreachable")


# ─────────────────────────────── адаптери баз ────────────────────────────

def _oa_abstract(inv: dict | None) -> str | None:
    if not inv:
        return None
    pos = {i: w for w, idxs in inv.items() for i in idxs}
    return " ".join(pos[i] for i in sorted(pos))


def _from_openalex(w: dict, qname: str, origin: str = "database") -> Record:
    src = ((w.get("primary_location") or {}).get("source") or {})
    return Record(
        title=w.get("display_name") or w.get("title") or "",
        year=w.get("publication_year"),
        doi=norm_doi(w.get("doi")),
        authors=[a["author"]["display_name"] for a in w.get("authorships", []) if a.get("author")],
        venue=src.get("display_name"),
        abstract=_oa_abstract(w.get("abstract_inverted_index")),
        url=w.get("id"),
        citations=w.get("cited_by_count"),
        source_db="openalex", query=qname, origin=origin,
        openalex_id=(w.get("id") or "").rsplit("/", 1)[-1] or None,
    )


def _oa_key() -> dict:
    k = os.environ.get("OPENALEX_API_KEY")
    return {"api_key": k} if k else {}


def _openalex_pages(flt: str, mailto: str, cap: int) -> Iterator[dict]:
    cursor, n = "*", 0
    while cursor and n < cap:
        data = json.loads(http_get("https://api.openalex.org/works",
                                   {"filter": flt, "per-page": 200, "cursor": cursor, "mailto": mailto, **_oa_key()}))
        results = data.get("results") or []
        if not results:
            break
        for w in results:
            yield w
            n += 1
            if n >= cap:
                return
        cursor = (data.get("meta") or {}).get("next_cursor")
        time.sleep(0.15)


def search_openalex(q: Query, cap: int, mailto: str, **_) -> Iterator[Record]:
    flt = (f"title_and_abstract.search:{render_boolean(q)},"
           f"from_publication_date:{DATE_FROM},to_publication_date:{DATE_TO}")
    for w in _openalex_pages(flt, mailto, cap):
        yield _from_openalex(w, q.name)


def search_s2(q: Query, cap: int, **_) -> Iterator[Record]:
    key = os.environ.get("S2_API_KEY")
    params = {"query": render_s2(q),
              "fields": "title,year,externalIds,venue,authors,abstract,citationCount,url",
              "publicationDateOrYear": f"{DATE_FROM}:{DATE_TO}"}
    token, n = None, 0
    while n < cap:
        p = dict(params, **({"token": token} if token else {}))
        data = json.loads(http_get("https://api.semanticscholar.org/graph/v1/paper/search/bulk",
                                   p, {"x-api-key": key} if key else None))
        for d in data.get("data") or []:
            yield Record(title=d.get("title") or "", year=d.get("year"),
                         doi=norm_doi((d.get("externalIds") or {}).get("DOI")),
                         authors=[a.get("name", "") for a in d.get("authors") or []],
                         venue=d.get("venue"), abstract=d.get("abstract"), url=d.get("url"),
                         citations=d.get("citationCount"), source_db="semantic_scholar", query=q.name)
            n += 1
            if n >= cap:
                return
        token = data.get("token")
        if not token:
            break
        time.sleep(0.3 if key else 1.2)


def search_arxiv(q: Query, cap: int, **_) -> Iterator[Record]:
    ns = {"a": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}
    sq = (f"({render_arxiv(q)}) AND submittedDate:"
          f"[{DATE_FROM.replace('-', '')}0000 TO {DATE_TO.replace('-', '')}2359]")
    start = 0
    while start < cap:
        root = ET.fromstring(http_get("https://export.arxiv.org/api/query",
                                      {"search_query": sq, "start": start, "max_results": min(100, cap - start)}))
        entries = root.findall("a:entry", ns)
        if not entries:
            break
        for e in entries:
            published = e.findtext("a:published", default="", namespaces=ns)
            yield Record(title=" ".join((e.findtext("a:title", default="", namespaces=ns)).split()),
                         year=int(published[:4]) if published[:4].isdigit() else None,
                         doi=norm_doi(e.findtext("arxiv:doi", default=None, namespaces=ns)),
                         authors=[a.findtext("a:name", default="", namespaces=ns)
                                  for a in e.findall("a:author", ns)],
                         venue="arXiv", abstract=(e.findtext("a:summary", default="", namespaces=ns)).strip(),
                         url=e.findtext("a:id", default=None, namespaces=ns), citations=None,
                         source_db="arxiv", query=q.name)
        start += len(entries)
        time.sleep(3.0)  # правила arXiv API


def search_crossref_acm(q: Query, cap: int, mailto: str, **_) -> Iterator[Record]:
    """Crossref не має булевої логіки: по фразі першої групи, далі локальний предикат.
    Ранжування в Crossref нечітке, тож cap обов'язковий — інакше він поверне все."""
    flt = f"from-pub-date:{DATE_FROM},until-pub-date:{DATE_TO},prefix:10.1145"
    per_phrase = max(cap // len(q.groups[0]), 1)
    for phrase in q.groups[0]:
        cursor, n, pages = "*", 0, 0
        while cursor and n < per_phrase and pages < 10:
            pages += 1
            data = json.loads(http_get("https://api.crossref.org/works", {
                "query.bibliographic": phrase, "filter": flt, "rows": 200, "cursor": cursor,
                "mailto": mailto,
                "select": "DOI,title,issued,author,container-title,abstract,is-referenced-by-count,URL"}))
            items = data["message"].get("items") or []
            if not items:
                break
            for it in items:
                title0 = (it.get("title") or [""])[0]
                if not _pattern(phrase).search(norm_text(title0)):
                    continue  # без анотацій булевий рядок можна перевірити лише по назві
                parts = ((it.get("issued") or {}).get("date-parts") or [[None]])[0]
                yield Record(title=(it.get("title") or [""])[0], year=parts[0] if parts else None,
                             doi=norm_doi(it.get("DOI")),
                             authors=[f"{a.get('given', '')} {a.get('family', '')}".strip()
                                      for a in it.get("author") or []],
                             venue=(it.get("container-title") or [None])[0],
                             abstract=strip_tags(it.get("abstract")), url=it.get("URL"),
                             citations=it.get("is-referenced-by-count"),
                             source_db="acm_via_crossref", query=q.name)
                n += 1
                if n >= per_phrase:
                    break
            cursor = data["message"].get("next-cursor")
            time.sleep(0.2)


def search_ieee(q: Query, cap: int, **_) -> Iterator[Record]:
    key = os.environ["IEEE_API_KEY"]
    start = 1
    while start <= cap:
        data = json.loads(http_get("https://ieeexploreapi.ieee.org/api/v1/search/articles", {
            "querytext": render_boolean(q), "apikey": key, "start_year": YEAR_FROM,
            "end_year": YEAR_TO, "max_records": 200, "start_record": start}))
        arts = data.get("articles") or []
        if not arts:
            break
        for a in arts:
            yield Record(title=a.get("title", ""),
                         year=int(a["publication_year"]) if str(a.get("publication_year", "")).isdigit() else None,
                         doi=norm_doi(a.get("doi")),
                         authors=[x.get("full_name", "") for x in (a.get("authors") or {}).get("authors", [])],
                         venue=a.get("publication_title"), abstract=a.get("abstract"),
                         url=a.get("html_url"), citations=a.get("citing_paper_count"),
                         source_db="ieee_xplore", query=q.name)
        start += len(arts)
        if start > int(data.get("total_records", 0)):
            break


def search_springer(q: Query, cap: int, **_) -> Iterator[Record]:
    key = os.environ["SPRINGER_API_KEY"]
    qq = f"({render_boolean(q)}) onlinedatefrom:{DATE_FROM} onlinedateto:{DATE_TO}"
    s = 1
    while s <= cap:
        data = json.loads(http_get("https://api.springernature.com/meta/v2/json",
                                   {"q": qq, "api_key": key, "s": s, "p": 100}))
        recs = data.get("records") or []
        if not recs:
            break
        for r in recs:
            yield Record(title=r.get("title", ""),
                         year=int(r["publicationDate"][:4]) if r.get("publicationDate") else None,
                         doi=norm_doi(r.get("doi")),
                         authors=[c.get("creator", "") for c in r.get("creators") or []],
                         venue=r.get("publicationName"), abstract=strip_tags(r.get("abstract")),
                         url=next((u.get("value") for u in r.get("url") or []), None), citations=None,
                         source_db="springer_link", query=q.name)
        s += len(recs)


def search_elsevier(q: Query, cap: int, **_) -> Iterator[Record]:
    """ScienceDirect Search API. Elsevier переводить пошук на PUT /v2 —
    перевірте актуальний ендпоінт у своєму обліковому записі перед прогоном."""
    key = os.environ["ELSEVIER_API_KEY"]
    start = 0
    while start < cap:
        data = json.loads(http_get("https://api.elsevier.com/content/search/sciencedirect",
                                   {"query": render_boolean(q), "date": f"{YEAR_FROM}-{YEAR_TO}",
                                    "count": 100, "start": start},
                                   {"X-ELS-APIKey": key, "Accept": "application/json"}))
        entries = (data.get("search-results") or {}).get("entry") or []
        if not entries or "error" in entries[0]:
            break
        for e in entries:
            cover = e.get("prism:coverDate") or ""
            yield Record(title=e.get("dc:title", ""), year=int(cover[:4]) if cover[:4].isdigit() else None,
                         doi=norm_doi(e.get("prism:doi")),
                         authors=[a.get("$", "") for a in ((e.get("authors") or {}).get("author") or [])
                                  if isinstance(a, dict)],
                         venue=e.get("prism:publicationName"), abstract=None,
                         url=e.get("prism:url"), citations=None,
                         source_db="sciencedirect", query=q.name)
        start += len(entries)


def import_scholar_csv(path: Path) -> list[Record]:
    """CSV з Publish or Perish (Google Scholar). Запити PoP формуйте вручну
    з основного й фасетних рядків; назву запиту беремо з колонки Query, якщо є."""
    out = []
    with path.open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            g = {k.strip().lower(): (v or "").strip() for k, v in row.items() if k}
            year = g.get("year", "")
            out.append(Record(title=g.get("title", ""), year=int(year) if year.isdigit() else None,
                              doi=norm_doi(g.get("doi")), authors=[a.strip() for a in g.get("authors", "").split(",") if a.strip()],
                              venue=g.get("source") or None, abstract=None, url=g.get("articleurl") or None,
                              citations=int(g["cites"]) if g.get("cites", "").isdigit() else None,
                              source_db="google_scholar_pop", query=g.get("query") or "primary"))
    return out


Adapter = Callable[..., Iterator[Record]]
ADAPTERS: dict[str, tuple[Adapter, str | None]] = {
    "openalex": (search_openalex, None),
    "semantic_scholar": (search_s2, None),
    "arxiv": (search_arxiv, None),
    "acm_via_crossref": (search_crossref_acm, None),
    "ieee_xplore": (search_ieee, "IEEE_API_KEY"),
    "springer_link": (search_springer, "SPRINGER_API_KEY"),
    "sciencedirect": (search_elsevier, "ELSEVIER_API_KEY"),
}


# ─────────────────────────────── дедуплікація ────────────────────────────

def _similar(a: str, b: str, thr: float) -> bool:
    if a == b:
        return True
    sm = SequenceMatcher(None, a, b)
    return sm.quick_ratio() >= thr and sm.ratio() >= thr


def deduplicate(records: list[Record], threshold: float = 0.93, prefix: str = "R") -> list[Candidate]:
    """DOI -> точний збіг; інакше нормалізована назва з нечітким порівнянням у вікні ±1 рік
    (препринт і публікація часто різняться роком). Саме ці дублікати «в різному
    форматуванні» стаття ловила лише на другому проході скринінгу."""
    clusters: list[list[Record]] = []
    by_doi: dict[str, int] = {}
    by_year: dict[int | None, list[int]] = defaultdict(list)
    for r in records:
        if r.doi and r.doi in by_doi:
            clusters[by_doi[r.doi]].append(r)
            continue
        tk, idx = title_key(r.title), None
        if tk:
            years = [r.year] if r.year is None else [r.year, r.year - 1, r.year + 1]
            for y in years:
                for ci in by_year.get(y, []):
                    if _similar(title_key(clusters[ci][0].title), tk, threshold):
                        idx = ci
                        break
                if idx is not None:
                    break
        if idx is None:
            idx = len(clusters)
            clusters.append([])
            by_year[r.year].append(idx)
        clusters[idx].append(r)
        if r.doi:
            by_doi.setdefault(r.doi, idx)
    return [_merge(c, i + 1, prefix) for i, c in enumerate(clusters)]


def _merge(cluster: list[Record], idx: int, prefix: str) -> Candidate:
    best = max(cluster, key=lambda r: (r.doi is not None, bool(r.abstract), r.citations or 0))
    first = lambda attr: getattr(best, attr) or next((getattr(r, attr) for r in cluster if getattr(r, attr)), None)
    cites = [r.citations for r in cluster if r.citations is not None]
    return Candidate(id=f"{prefix}{idx:04d}", title=best.title, year=first("year"), doi=first("doi"),
                     venue=first("venue"), abstract=first("abstract"),
                     citations=max(cites) if cites else None, openalex_id=first("openalex_id"),
                     source_dbs={r.source_db for r in cluster}, queries={r.query for r in cluster},
                     origins={r.origin for r in cluster}, n_raw=len(cluster))


# ─────────────────────────── прапорці для скринінгу ──────────────────────

def auto_flag(c: Candidate) -> str:
    """Лише підказка для людини. Порядок — від найсильнішого сигналу."""
    title = norm_text(c.title)
    if any(_pattern(t).search(title) for t in PE_TERMS):
        return "pe-in-title"
    text = f"{c.title} {c.abstract or ''}"
    if matches(QUERIES[0], text):
        return "primary-match"
    if any(matches(q, text) for q in QUERIES[1:]):
        return "facet-match"
    return "no-match" if c.abstract else "no-match-title-only"


def in_window(c: Candidate) -> str:
    if c.year is None:
        return "unknown"
    return "yes" if YEAR_FROM <= c.year <= YEAR_TO else "no"


DECISIONS = {"INC", "OUT", "EC1", "EC2", "EC3", "EC4", "EC5"}
SCREEN_FIELDS = ["id", "title", "year", "in_window", "doi", "venue", "citations", "source_dbs",
                 "queries", "origin", "auto_flag", "pass1", "pass1_date", "pass2", "pass2_date",
                 "notes", "openalex_id", "abstract"]


def write_screening(cands: list[Candidate], path: Path) -> None:
    order = {"pe-in-title": 0, "primary-match": 1, "facet-match": 2,
             "no-match-title-only": 3, "no-match": 4}
    rows = sorted(cands, key=lambda c: (order[auto_flag(c)], -(c.citations or 0)))
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=SCREEN_FIELDS)
        w.writeheader()
        for c in rows:
            w.writerow({"id": c.id, "title": c.title, "year": c.year or "", "in_window": in_window(c),
                        "doi": c.doi or "", "venue": c.venue or "", "citations": c.citations if c.citations is not None else "",
                        "source_dbs": ";".join(sorted(c.source_dbs)), "queries": ";".join(sorted(c.queries)),
                        "origin": ";".join(sorted(c.origins)), "auto_flag": auto_flag(c),
                        "pass1": "", "pass1_date": "", "pass2": "", "pass2_date": "", "notes": "",
                        "openalex_id": c.openalex_id or "", "abstract": c.abstract or ""})


def read_csv(path: Path) -> list[dict]:
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def final_decision(r: dict) -> str:
    return (r.get("pass2") or r.get("pass1") or "").strip().upper()


# ─────────────────────────────── команди ─────────────────────────────────

def cmd_search(args) -> None:
    global USER_AGENT
    USER_AGENT = f"mlr-pe-search/1.0 (mailto:{args.mailto})"
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    dbs = args.dbs.split(",") if args.dbs else [d for d in ADAPTERS if d != "acm_via_crossref"]
    raw: list[Record] = []
    log: dict = {"protocol": {"date_from": DATE_FROM, "date_to": DATE_TO,
                              "queries": {q.name: render_boolean(q) for q in QUERIES}},
                 "databases": {}}

    for db in dbs:
        fn, env = ADAPTERS[db]
        if env and not os.environ.get(env):
            log["databases"][db] = {"status": f"skipped: {env} not set"}
            print(f"[{db}] пропущено — немає {env}", file=sys.stderr)
            continue
        log["databases"][db] = {"status": "ok", "per_query": {}}
        for q in QUERIES:
            stats = Counter()
            try:
                for r in fn(q, cap=args.cap, mailto=args.mailto):
                    raw.append(r)
                    stats["returned"] += 1
                    if not r.abstract:
                        stats["no_abstract"] += 1
                    elif not matches(q, f"{r.title} {r.abstract}"):
                        # база повернула, але той самий булевий рядок локально не виконується
                        stats["fails_local_predicate"] += 1
            except Exception as e:  # база не впала мовчки в нуль — помилка видима у звіті
                stats["error"] = 1
                log["databases"][db]["status"] = f"partial: {type(e).__name__}: {e}"
                print(f"[{db}/{q.name}] помилка: {e}", file=sys.stderr)
            log["databases"][db]["per_query"][q.name] = dict(stats)
            print(f"[{db}/{q.name}] {stats['returned']} записів", file=sys.stderr)

    if args.scholar_csv:
        gs = import_scholar_csv(Path(args.scholar_csv))
        raw.extend(gs)
        log["databases"]["google_scholar_pop"] = {"status": "imported", "returned": len(gs)}

    finish_search(raw, out, log)


def finish_search(raw: list[Record], out: Path, log: dict) -> list[Candidate]:
    with (out / "raw_records.jsonl").open("w", encoding="utf-8") as f:
        for r in raw:
            f.write(json.dumps(asdict(r), ensure_ascii=False) + "\n")
    cands = deduplicate(raw)
    write_screening(cands, out / "screening.csv")
    flags = Counter(auto_flag(c) for c in cands)
    log["flow"] = {
        "identified_raw": len(raw),
        "identified_by_db": dict(Counter(r.source_db for r in raw)),
        "after_dedup": len(cands),
        "duplicates_removed": len(raw) - len(cands),
        "acm_doi_10_1145": sum((c.doi or "").startswith("10.1145/") for c in cands),
        "auto_flags": dict(flags),
        "outside_date_window": sum(in_window(c) == "no" for c in cands),
        "note": "Скринінг за назвами виконує людина в screening.csv (pass1/pass2). "
                "Коди: INC, OUT (нерелевантно за назвою), EC1–EC5.",
    }
    (out / "prisma_search.json").write_text(json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(log["flow"], ensure_ascii=False, indent=2))
    return cands


def cohen_kappa(a: list[str], b: list[str]) -> tuple[float, float]:
    n = len(a)
    po = sum(x == y for x, y in zip(a, b)) / n
    ca, cb = Counter(a), Counter(b)
    pe = sum(ca[l] * cb[l] for l in set(a) | set(b)) / n ** 2
    return po, ((po - pe) / (1 - pe) if pe < 1 else float("nan"))


def cmd_agreement(args) -> None:
    rows = read_csv(Path(args.screening))
    both = [r for r in rows if r["pass1"].strip() and r["pass2"].strip()]
    if not both:
        sys.exit("Немає рядків із заповненими pass1 і pass2.")
    bad = {r["pass1"].strip().upper() for r in both} | {r["pass2"].strip().upper() for r in both}
    if bad - DECISIONS:
        sys.exit(f"Невідомі коди рішень: {sorted(bad - DECISIONS)}")
    p1 = [r["pass1"].strip().upper() for r in both]
    p2 = [r["pass2"].strip().upper() for r in both]
    po, k = cohen_kappa(p1, p2)
    bin1 = ["INC" if x == "INC" else "EXC" for x in p1]
    bin2 = ["INC" if x == "INC" else "EXC" for x in p2]
    bpo, bk = cohen_kappa(bin1, bin2)
    dis = [(r["id"], a, b) for r, a, b in zip(both, p1, p2) if a != b]
    substantive = [d for d in dis if (d[1] == "INC") != (d[2] == "INC")]

    print(f"Рядків з обома проходами: {len(both)}")
    print(f"За кодами (INC/OUT/EC1–5):  збіг {po:.1%}, kappa = {k:.3f}")
    print(f"Бінарно (включити/ні):      збіг {bpo:.1%}, kappa = {bk:.3f}")
    print(f"Розбіжностей: {len(dis)}, із них змінюють рішення про включення: {len(substantive)}")
    for d in dis:
        print(f"  {d[0]}: {d[1]} -> {d[2]}")
    print("\nУвага: два проходи одного рецензента вимірюють узгодженість, а не відсутність "
          "упередження — систематична помилка відтворюється через два тижні так само.")
    sample = random.Random(42).sample([r["id"] for r in both], k=min(25, len(both)))
    print(f"Вибірка для незалежного другого рецензента (seed=42, n={len(sample)}):")
    print("  " + ", ".join(sorted(sample)))


def _openalex_get(path: str, mailto: str) -> dict:
    return json.loads(http_get(f"https://api.openalex.org/{path}", {"mailto": mailto, **_oa_key()}))


def cmd_snowball(args) -> None:
    global USER_AGENT
    USER_AGENT = f"mlr-pe-search/1.0 (mailto:{args.mailto})"
    rows = read_csv(Path(args.screening))
    included = [r for r in rows if final_decision(r) == "INC" and (r["doi"] or r["openalex_id"])]
    if not included:
        sys.exit("Немає включених (INC) джерел із DOI або OpenAlex ID.")
    seeds = sorted(included, key=lambda r: -int(r["citations"] or 0))[:args.seeds]
    known_dois = {norm_doi(r["doi"]) for r in rows if r["doi"]}
    known_titles = [title_key(r["title"]) for r in rows]

    found: list[Record] = []
    per_seed = {}
    flt_dates = f"from_publication_date:{DATE_FROM},to_publication_date:{DATE_TO}"
    for s in seeds:
        ref = f"works/doi:{urllib.parse.quote(s['doi'], safe='/:.()')}" if s["doi"] else f"works/{s['openalex_id']}"
        try:
            w = _openalex_get(ref, args.mailto)
        except Exception as e:
            per_seed[s["id"]] = {"error": str(e)}
            continue
        wid = w["id"].rsplit("/", 1)[-1]
        fwd = [_from_openalex(x, "snowball", f"snowball-forward:{s['id']}")
               for x in _openalex_pages(f"cites:{wid},{flt_dates}", args.mailto, args.forward_cap)]
        refs = [u.rsplit("/", 1)[-1] for u in w.get("referenced_works") or []]
        bwd: list[Record] = []
        for i in range(0, len(refs), 50):
            flt = f"ids.openalex:{'|'.join(refs[i:i + 50])}"
            bwd += [_from_openalex(x, "snowball", f"snowball-backward:{s['id']}")
                    for x in _openalex_pages(flt, args.mailto, 50)]
        bwd_in = [r for r in bwd if r.year and YEAR_FROM <= r.year <= YEAR_TO]
        per_seed[s["id"]] = {"title": s["title"], "citations": s["citations"],
                             "forward": len(fwd), "backward_total": len(bwd),
                             "backward_in_window": len(bwd_in)}
        found += fwd + bwd_in

    def is_known(r: Record) -> bool:
        if r.doi and r.doi in known_dois:
            return True
        tk = title_key(r.title)
        return any(_similar(tk, k, 0.93) for k in known_titles if k)

    new = [r for r in found if not is_known(r)]
    cands = deduplicate(new, prefix="B")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    write_screening(cands, out / "snowball_screening.csv")
    rep = {"seeds": per_seed, "retrieved": len(found), "already_known": len(found) - len(new),
           "new_unique_candidates": len(cands), "auto_flags": dict(Counter(auto_flag(c) for c in cands)),
           "note": "Тематичний дрейф: перевірте, скільки нових кандидатів мають лише facet-match "
                   "чи no-match — це література про DevEx/DORA без PE."}
    (out / "snowball_report.json").write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(rep, ensure_ascii=False, indent=2))


# ─────────────────────── 3.2.2 сіра література ───────────────────────────

AACODS = ("authority", "accuracy", "coverage", "objectivity", "date", "significance")


@dataclass
class GraySource:
    key: str
    title: str
    years: tuple[int, int]
    kind: str                       # survey | whitepaper | framework | case-study | book
    sample_size: int | None
    sample_unit: str                # persons | leaders | teams | organizations | committee | none
    vendor: bool
    methodology_doc: bool | None = None   # None = не перевірено
    demographics_doc: bool | None = None
    in_reference_list: bool = True
    note: str = ""
    aacods: dict[str, int | None] = field(default_factory=lambda: {d: None for d in AACODS})

    def total(self) -> int | None:
        vals = list(self.aacods.values())
        return None if any(v is None for v in vals) else sum(vals)


# Таблиця 1 статті. methodology_doc / demographics_doc і бали AACODS навмисно
# не заповнені: їх треба виставити, прочитавши самі звіти.
GRAY_TABLE_1: list[GraySource] = [
    GraySource("cncf-wp", "CNCF Platforms White Paper", (2023, 2023), "whitepaper", None, "committee", False),
    GraySource("cncf-mm", "CNCF PE Maturity Model", (2023, 2023), "framework", None, "committee", False),
    GraySource("dora-2024", "DORA Accelerate State of DevOps", (2024, 2024), "survey", 39000, "persons", True,
               note="Google Cloud; методологія публікується"),
    GraySource("puppet-2024", "Puppet State of DevOps", (2024, 2024), "survey", 470, "persons", True,
               note="джерело цифри 94% у вступі"),
    GraySource("port-2024", "Port State of IDPs", (2024, 2024), "survey", 100, "leaders", True,
               note="вступ подає як дані про розробників (70%, 3–4 год)"),
    GraySource("humanitec-v1-4", "Humanitec State of PE Vol. 1–4", (2022, 2025), "survey", 300, "teams", True),
    GraySource("atlassian-dx-2024", "Atlassian/DX State of DevEx", (2024, 2024), "survey", 2100, "persons", True,
               in_reference_list=False),
    GraySource("redhat-2024", "Red Hat State of PE & AI", (2024, 2024), "survey", 1000, "persons", True),
    GraySource("spotify-backstage", "Spotify Backstage blogs", (2020, 2025), "case-study", 3000, "organizations", True,
               note="користувачі продукту, а не респонденти"),
    GraySource("team-topologies", "Team Topologies (book)", (2019, 2019), "book", None, "none", False),
    GraySource("accelerate", "Accelerate (book)", (2018, 2018), "book", None, "none", False),
]


def check_gray(g: GraySource) -> list[str]:
    issues = []
    if g.years[0] < YEAR_FROM:
        issues.append(f"IC1: {g.years[0]} поза вікном {YEAR_FROM}–{YEAR_TO}")
    if g.kind == "survey":
        if g.sample_size is None:
            issues.append("3.2.2: опитування без розміру вибірки")
        for fld, label in (("methodology_doc", "методологія"), ("demographics_doc", "демографія")):
            v = getattr(g, fld)
            if v is False:
                issues.append(f"3.2.2: {label} не задокументована")
            elif v is None:
                issues.append(f"3.2.2: {label} — не перевірено")
    if g.sample_unit not in ("persons", "none", "committee"):
        issues.append(f"одиниця вибірки '{g.sample_unit}' — не порівнювана з людьми")
    if g.kind == "book":
        issues.append("книга: за Garousi et al. навряд чи сіра література")
    if g.vendor:
        issues.append("вендор: Objectivity має відображати комерційний інтерес")
    if not g.in_reference_list:
        issues.append("немає у списку літератури")
    t = g.total()
    if t is None:
        issues.append("AACODS не оцінено")
    elif t < 4:
        issues.append(f"AACODS {t}/12 < 4 → EC1")
    return issues


def gray_tier(g: GraySource) -> str:
    t = g.total()
    return "?" if t is None else ("C" if t >= 7 else "D" if t >= 4 else "excl")


def cmd_gray(_args=None) -> None:
    print(f"{'ключ':<20}{'роки':<11}{'тип':<12}{'вибірка':>8} {'одиниця':<14}{'рівень':<7}зауваги")
    for g in GRAY_TABLE_1:
        yrs = f"{g.years[0]}" if g.years[0] == g.years[1] else f"{g.years[0]}–{g.years[1]}"
        n = f"{g.sample_size:,}".replace(",", " ") if g.sample_size else "—"
        issues = check_gray(g)
        print(f"{g.key:<20}{yrs:<11}{g.kind:<12}{n:>8} {g.sample_unit:<14}{gray_tier(g):<7}"
              f"{issues[0] if issues else 'OK'}")
        for i in issues[1:]:
            print(f"{'':<72}{i}")
    units = defaultdict(list)
    for g in GRAY_TABLE_1:
        if g.sample_size:
            units[g.sample_unit].append(g.key)
    print("\nПорівнювані між собою групи (одна одиниця вибірки):")
    for u, keys in units.items():
        print(f"  {u}: {', '.join(keys)}")


# ───────────────────────────────── звіт ──────────────────────────────────

def _stream_stats(rows: list[dict]) -> dict:
    dec = Counter(final_decision(r) or "UNSCREENED" for r in rows)
    passed_title = sum(v for k, v in dec.items() if k not in ("OUT", "UNSCREENED"))
    inc = dec["INC"]
    return {"after_dedup": len(rows), "unscreened": dec["UNSCREENED"],
            "passed_title_screening": passed_title, "included": inc,
            "rate_vs_after_dedup": round(inc / len(rows), 3) if rows else None,
            "rate_vs_title_screened": round(inc / passed_title, 3) if passed_title else None,
            "decisions": dict(dec)}


def cmd_report(args) -> None:
    acad = _stream_stats(read_csv(Path(args.screening)))
    snow = _stream_stats(read_csv(Path(args.snowball))) if args.snowball else None
    gray_in = [g.key for g in GRAY_TABLE_1 if (g.total() or 0) >= 4]
    gray_unscored = [g.key for g in GRAY_TABLE_1 if g.total() is None]
    total = acad["included"] + (snow["included"] if snow else 0) + len(gray_in)
    rep = {"academic_database_stream": acad, "snowball_stream": snow,
           "gray_literature": {"registry": len(GRAY_TABLE_1), "included_aacods_ge_4": len(gray_in),
                               "unscored": gray_unscored},
           "total_corpus": total,
           "warning": "Рівень включення має сенс лише всередині потоку. Ділити загальний корпус "
                      "(з сірою літературою) на кількість академічних кандидатів некоректно."}
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    if acad["unscreened"] or (snow and snow["unscreened"]):
        print("\nУвага: є непереглянуті рядки — числа попередні.", file=sys.stderr)


# ─────────────────────────────── офлайн-демо ─────────────────────────────

def _demo_records() -> list[Record]:
    R = lambda **kw: Record(**{"authors": [], "venue": None, "url": None, "citations": None,
                               "abstract": None, "doi": None, "origin": "database", **kw})
    return [
        R(title="Full spec software via platform engineering", year=2023, doi="10.1145/3593434.3593440",
          abstract="We describe platform engineering for DevOps teams and software development at scale.",
          citations=3, source_db="openalex", query="primary"),
        R(title="Full Spec Software via Platform Engineering.", year=2023, doi="https://doi.org/10.1145/3593434.3593440",
          citations=2, source_db="semantic_scholar", query="primary"),
        R(title="Paving the path towards platform engineering using a comprehensive reference model", year=2024,
          doi="10.1007/978-3-031-54712-6_11", citations=2, source_db="springer_link", query="primary",
          abstract="A reference model for platform engineering spanning infrastructure, runtime and cloud delivery."),
        R(title="Paving the Path Towards Platform-Engineering Using a Comprehensive Reference Model", year=2023,
          source_db="google_scholar_pop", query="primary"),
        R(title="The SPACE of developer productivity", year=2021, doi="10.1145/3454122.3454124", citations=900,
          abstract="Developer productivity is multidimensional; the SPACE framework captures it.",
          source_db="openalex", query="metrics"),
        R(title="The design and realization of a self-hosted and open-source agile internal development platform",
          year=2025, doi="10.1109/ACCESS.2025.3564141", citations=4, source_db="ieee_xplore", query="primary",
          abstract="An internal developer platform built on cloud and kubernetes for agile teams."),
        R(title="Autoscaling Kubernetes clusters at the edge", year=2022, doi="10.1000/demo.1", citations=10,
          abstract="We study cloud autoscaling for kubernetes clusters.", source_db="openalex", query="primary"),
        R(title="Team Topologies: Organizing Business and Technology Teams for Fast Flow", year=2019,
          source_db="google_scholar_pop", query="organizational"),
        R(title="Port: an approach to porting legacy code", year=2021, doi="10.1000/demo.2",
          abstract="Porting legacy software to new architectures.", source_db="semantic_scholar", query="tools"),
    ]


def cmd_demo(args) -> None:
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    print("== search (синтетичні записи, без мережі) ==")
    finish_search(_demo_records(), out, {"protocol": "demo", "databases": {}})

    # імітація двох проходів скринінгу
    rows = read_csv(out / "screening.csv")
    for r in rows:
        p = "INC" if r["auto_flag"] in ("pe-in-title", "primary-match") else "OUT"
        r["pass1"], r["pass1_date"] = p, "2026-01-05"
        r["pass2"], r["pass2_date"] = p, "2026-01-19"
    rows[-1]["pass2"] = "EC4"                    # технічна розбіжність
    if len(rows) > 2:
        rows[2]["pass2"] = "EC2"                 # змістовна розбіжність
    with (out / "screening.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=SCREEN_FIELDS)
        w.writeheader()
        w.writerows(rows)

    print("\n== agreement ==")
    cmd_agreement(argparse.Namespace(screening=str(out / "screening.csv")))
    print("\n== report ==")
    cmd_report(argparse.Namespace(screening=str(out / "screening.csv"), snowball=None))
    print("\n== gray (3.2.2) ==")
    cmd_gray()


# ─────────────────────────────────── CLI ─────────────────────────────────

def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description="MLR §3.2: стратегія пошуку (PE/IDP)")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("search", help="3.2.1: пошук у базах + дедуплікація + CSV для скринінгу")
    s.add_argument("--out", required=True)
    s.add_argument("--mailto", required=True, help="e-mail для ввічливого доступу OpenAlex/Crossref")
    s.add_argument("--dbs", help=f"через кому, з: {','.join(ADAPTERS)}")
    s.add_argument("--cap", type=int, default=2000, help="максимум записів на (базу × запит)")
    s.add_argument("--scholar-csv", help="CSV з Publish or Perish")
    s.set_defaults(func=cmd_search)

    a = sub.add_parser("agreement", help="Cohen's kappa між pass1 і pass2")
    a.add_argument("--screening", required=True)
    a.set_defaults(func=cmd_agreement)

    b = sub.add_parser("snowball", help="3.2.1: прямий/зворотний сніговий ком через OpenAlex")
    b.add_argument("--screening", required=True)
    b.add_argument("--out", required=True)
    b.add_argument("--mailto", required=True)
    b.add_argument("--seeds", type=int, default=10)
    b.add_argument("--forward-cap", type=int, default=500)
    b.set_defaults(func=cmd_snowball)

    g = sub.add_parser("gray", help="3.2.2: реєстр сірої літератури і перевірка критеріїв")
    g.set_defaults(func=cmd_gray)

    r = sub.add_parser("report", help="потік відбору з явними знаменниками")
    r.add_argument("--screening", required=True)
    r.add_argument("--snowball")
    r.set_defaults(func=cmd_report)

    d = sub.add_parser("demo", help="офлайн-перевірка конвеєра на синтетичних даних")
    d.add_argument("--out", default="demo")
    d.set_defaults(func=cmd_demo)

    args = p.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
