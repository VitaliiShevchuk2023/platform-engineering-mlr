#!/usr/bin/env python3
"""
Interactive screening of one pass file, one record at a time.

Shows the title, venue, year, automatic flag and abstract of each record that
has no decision yet, takes the decision as a single key, stamps today's date
and saves the file after every decision. Only the given pass file is read, so
the other pass's decisions are never shown.

Allowed codes depend on the screening stage (PROTOCOL.md, "Коди рішень"):
  --stage ta    title and abstract: INC, OUT, EC2, EC3 (default)
  --stage full  full text:          INC, EC2, EC3, EC4, EC5, EC6
EC1 (quality below threshold) belongs to quality assessment, not screening.

Run from the repository root:
    python screen.py data/screening/pass1.csv
"""
from __future__ import annotations

import argparse
import csv
import os
import shutil
import sys
import textwrap
from datetime import date
from pathlib import Path

STAGES = {
    "ta": {"i": "INC", "o": "OUT", "2": "EC2", "3": "EC3"},
    "full": {"i": "INC", "2": "EC2", "3": "EC3", "4": "EC4", "5": "EC5", "6": "EC6"},
}
HINTS = {
    "INC": "включити / передати далі (у сумніві — сюди)",
    "OUT": "інше значення терміна",
    "EC2": "PE/IDP лише згадується",
    "EC3": "не публікація з власним змістом",
    "EC4": "дублікат або попередня версія",
    "EC5": "не англійською / немає тексту",
    "EC6": "видання без рецензування",
}
BOLD, DIM, RESET = ("\033[1m", "\033[2m", "\033[0m") if sys.stdout.isatty() else ("", "", "")


def getch() -> str:
    """Read one key without Enter on a terminal; fall back to a line otherwise."""
    if not sys.stdin.isatty():
        line = sys.stdin.readline()
        return line.strip()[:1] if line else "q"
    import termios
    import tty
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ch = sys.stdin.read(1)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
    if ch == "\x03":  # Ctrl+C
        raise KeyboardInterrupt
    return ch


def save(path: Path, fields: list[str], rows: list[dict]) -> None:
    """Write to a temporary file and replace atomically, so a crash never leaves half a file."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    os.replace(tmp, path)


def show(r: dict, n_done: int, total: int, keys: dict[str, str]) -> None:
    width = min(shutil.get_terminal_size((100, 30)).columns, 110)
    print("\n" + "─" * width)
    print(f"{DIM}{n_done}/{total} · {r['id']} · {r.get('auto_flag', '')} · "
          f"{r.get('year', '')} · {r.get('venue') or 'видання не вказано'}{RESET}")
    print(BOLD + textwrap.fill(r.get("title", ""), width) + RESET)
    abstract = (r.get("abstract") or "").strip() or "(анотації немає)"
    print(textwrap.fill(" ".join(abstract.split()), width))
    legend = "  ".join(f"[{k}] {c}" for k, c in keys.items())
    print(f"\n{legend}  [n] примітка  [s] пропустити  [u] скасувати  [?] коди  [q] вийти")


def main() -> None:
    ap = argparse.ArgumentParser(description="Покроковий скринінг одного файлу проходу")
    ap.add_argument("pass_file")
    ap.add_argument("--stage", choices=STAGES, default="ta")
    args = ap.parse_args()

    path = Path(args.pass_file)
    with path.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        fields = list(reader.fieldnames or [])
        rows = list(reader)
    missing = {"id", "decision", "date", "notes"} - set(fields)
    if missing:
        sys.exit(f"{path}: немає колонок {sorted(missing)}; файл має бути з pass-template")

    keys = STAGES[args.stage]
    today = date.today().isoformat()
    total = len(rows)
    queue = [r for r in rows if not (r["decision"] or "").strip()]
    history: list[dict] = []   # decided in this session, for undo
    skipped = 0
    pos = 0
    note = ""

    print(f"{path}: {total} записів, без рішення {len(queue)}. Етап: {args.stage}.")
    try:
        while pos < len(queue):
            r = queue[pos]
            show(r, total - len(queue) + pos, total, keys)
            if note:
                print(f"{DIM}примітка: {note}{RESET}")
            ch = getch().lower()
            if ch in keys:
                r["decision"], r["date"], r["notes"] = keys[ch], today, note
                history.append(r)
                note = ""
                pos += 1
                save(path, fields, rows)
            elif ch == "n":
                note = input("\nПримітка: ").strip()
            elif ch == "s":
                skipped += 1
                note = ""
                pos += 1
            elif ch == "u":
                if history:
                    last = history.pop()
                    last["decision"] = last["date"] = last["notes"] = ""
                    save(path, fields, rows)
                    pos = queue.index(last)
                    print(f"\nСкасовано рішення для {last['id']}.")
                else:
                    print("\nУ цьому сеансі ще немає рішень.")
            elif ch == "?":
                print()
                for c in keys.values():
                    print(f"  {c:<4} {HINTS[c]}")
                print("  Перший код, що підходить, у порядку таблиці з PROTOCOL.md.")
            elif ch == "q":
                break
    except KeyboardInterrupt:
        print("\nПерервано; усі рішення до цього моменту збережено.")

    done = sum(1 for r in rows if (r["decision"] or "").strip())
    print(f"\nРішень у файлі: {done} з {total}; у цьому сеансі: {len(history)}, пропущено: {skipped}.")
    if history:
        print(f'Далі: git add {path} && git commit -m "Pass: {done}/{total}" && git push')


if __name__ == "__main__":
    main()