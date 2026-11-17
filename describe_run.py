#!/usr/bin/env python3
"""
Descriptive statistics for a single search run (before screening).

Reads prisma_search.json, raw_records.jsonl and screening.csv from a run
directory and writes summary.md to the same directory. It neither includes
nor excludes records: it only describes what was found, where it came from,
and which quality signals it carries.

Run from the repository root:
    python describe_run.py data/runs/<run>
"""
from __future__ import annotations

import csv
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

from mlr_search import title_key

# Preprint servers: records from them go to the grey-literature stream (PROTOCOL.md)
PREPRINT_VENUES = ("ssrn", "zenodo", "arxiv", "research square", "preprints",
                   "techrxiv", "biorxiv", "medrxiv", "osf preprints", "authorea")


def pct(a: int, b: int) -> str:
    return f"{100 * a / b:.0f}%" if b else "—"


def table(headers: list[str], rows: list[list]) -> list[str]:
    out = ["| " + " | ".join(headers) + " |",
           "|" + "|".join("---" for _ in headers) + "|"]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return out + [""]


def main(run: Path) -> None:
    prisma = json.loads((run / "prisma_search.json").read_text(encoding="utf-8"))
    raw = [json.loads(line) for line in
           (run / "raw_records.jsonl").read_text(encoding="utf-8").splitlines() if line]
    with (run / "screening.csv").open(encoding="utf-8") as f:
        cands = list(csv.DictReader(f))
    flow = prisma["flow"]
    md: list[str] = [f"# Опис прогону `{run.name}`", ""]

    proto = prisma.get("protocol", {})
    if isinstance(proto, dict):
        md += [f"Вікно дат: {proto.get('date_from')} — {proto.get('date_to')}", ""]

    # 1. Selection flow
    md += ["## 1. Потік до скринінгу", ""]
    md += table(["Етап", "Записів"], [
        ["Знайдено в базах (сирі записи)", flow["identified_raw"]],
        ["Дублікатів вилучено", flow["duplicates_removed"]],
        ["Кандидатів на скринінг", flow["after_dedup"]],
    ])

    # 2. Databases: completeness and divergence from the boolean string
    md += ["## 2. Бази даних", "",
           "`не проходить предикат` — база повернула запис, але той самий булевий "
           "рядок локально для назви й анотації не виконується.", ""]
    rows = []
    for db, v in prisma["databases"].items():
        pq = v.get("per_query") or {}
        ret = sum(q.get("returned", 0) for q in pq.values())
        fails = sum(q.get("fails_local_predicate", 0) for q in pq.values())
        noabs = sum(q.get("no_abstract", 0) for q in pq.values())
        rows.append([db, v.get("status", ""), ret, f"{fails} ({pct(fails, ret - noabs)})",
                     f"{noabs} ({pct(noabs, ret)})"])
    md += table(["База", "Статус", "Записів", "Не проходить предикат", "Без анотації"], rows)
    bad = [r[0] for r in rows if not str(r[1]).startswith(("ok", "skipped", "imported"))]
    if bad:
        md += [f"**⚠️ Неповні бази: {', '.join(bad)}. Прогін не придатний як офіційний.**", ""]

    # 3. Database overlap
    md += ["## 3. Перетин баз", ""]
    combos = Counter(c["source_dbs"] for c in cands)
    md += table(["Бази", "Кандидатів"], [[k, n] for k, n in combos.most_common()])
    only = Counter(c["source_dbs"] for c in cands if ";" not in c["source_dbs"])
    md += ["Кандидатів, знайдених лише однією базою: "
           + ", ".join(f"{k} — {n}" for k, n in only.most_common()), ""]

    # 4. Queries and automatic flags
    md += ["## 4. Запити й автоматичні позначки", ""]
    q = Counter(x for c in cands for x in c["queries"].split(";") if x)
    md += table(["Запит", "Кандидатів"], [[k, n] for k, n in q.most_common()])
    flags = Counter(c["auto_flag"] for c in cands)
    md += table(["Позначка", "Кандидатів", "Частка"],
                [[k, n, pct(n, len(cands))] for k, n in flags.most_common()])

    # 5. Publication years
    md += ["## 5. Роки публікації", ""]
    years = Counter(c["year"] or "—" for c in cands)
    md += table(["Рік", "Кандидатів"], [[y, years[y]] for y in sorted(years)])

    # 6. Venues
    md += ["## 6. Видання", ""]
    def is_preprint(v: str) -> bool:
        return any(p in (v or "").lower() for p in PREPRINT_VENUES)
    pre = sum(is_preprint(c["venue"]) for c in cands)
    novenue = sum(not c["venue"] for c in cands)
    md += [f"Сховища препринтів: {pre} ({pct(pre, len(cands))}); "
           f"видання не вказано: {novenue} ({pct(novenue, len(cands))}).", ""]
    venues = Counter(c["venue"] for c in cands if c["venue"])
    md += ["20 найчастіших видань (рецензованість перевіряється вручну за DBLP/Scopus, "
           "результат — у `data/quality/venues.csv`):", ""]
    md += table(["Видання", "Кандидатів", "Препринт"],
                [[v, n, "так" if is_preprint(v) else ""] for v, n in venues.most_common(20)])

    # 7. Source independence: clusters by first author
    md += ["## 7. Авторські кластери", ""]
    first_author: dict[str, str] = {}
    for r in raw:
        if r.get("authors"):
            first_author.setdefault(title_key(r["title"]), r["authors"][0])
    by_author: dict[str, list[str]] = defaultdict(list)
    for c in cands:
        a = first_author.get(title_key(c["title"]))
        if a:
            by_author[a].append(c["title"])
    clusters = sorted(((a, t) for a, t in by_author.items() if len(t) >= 3),
                      key=lambda x: -len(x[1]))
    with_author = sum(len(t) for t in by_author.values())
    md += [f"Кандидатів з відомим першим автором: {with_author}; "
           f"незалежних авторських груп: {len(by_author)}.", ""]
    if clusters:
        md += ["Перші автори з трьома й більше кандидатами:", ""]
        md += table(["Перший автор", "Кандидатів", "Приклад назви"],
                    [[a, len(t), t[0][:80]] for a, t in clusters])
    else:
        md += ["Перших авторів з трьома й більше кандидатами немає.", ""]

    # 8. Known items (recall check)
    md += ["## 8. Відомі записи (перевірка повноти)", ""]
    known = Path("known_items.txt")
    if known.exists():
        dois = {(r.get("doi") or "").lower() for r in raw}
        items = [x.strip().lower() for x in known.read_text(encoding="utf-8").splitlines()
                 if x.strip() and not x.startswith("#")]
        found = [d for d in items if d in dois]
        md += table(["DOI", "Знайдено"], [[d, "✓" if d in dois else "✗"] for d in items])
        md += [f"Знайдено {len(found)} з {len(items)}."
               + ("" if len(found) == len(items) else
                  " **Кожен відсутній запис звітується з поясненням причини.**"), ""]
    else:
        md += ["`known_items.txt` не знайдено.", ""]

    out = run / "summary.md"
    out.write_text("\n".join(md), encoding="utf-8")
    print("\n".join(md))
    print(f"\nЗбережено: {out}", file=sys.stderr)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("Використання: python describe_run.py <тека прогону>")
    main(Path(sys.argv[1]))