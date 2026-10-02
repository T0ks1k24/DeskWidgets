#!/usr/bin/env python3
"""Fetch NBU hryvnia exchange rates into the currency widget's key=value cache.

Usage: fetch.py CACHE
Writes  date=DD.MM.YYYY  and  CODE=rate|previous day's rate|name  for every currency.
On any network/parse error the old cache is kept untouched.
"""
import json
import os
import sys
import urllib.request
from datetime import datetime, timedelta

API = "https://bank.gov.ua/NBUStatService/v1/statdirectory/exchange?json"
CACHE = sys.argv[1] if len(sys.argv) > 1 else os.path.expanduser("~/.cache/nbu-rates")


def fetch(date=None):
    url = API + (f"&date={date:%Y%m%d}" if date else "")
    req = urllib.request.Request(url, headers={"User-Agent": "deskwidgets/1.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.load(resp)


def main() -> None:
    today = fetch()
    day = datetime.strptime(today[0]["exchangedate"], "%d.%m.%Y")
    prev = {r["cc"]: r["rate"] for r in fetch(day - timedelta(days=1))}
    lines = [f"date={today[0]['exchangedate']}"]
    for r in today:
        name = " ".join(str(r["txt"]).split()).replace("|", "/")
        lines.append(f"{r['cc']}={r['rate']}|{prev.get(r['cc'], r['rate'])}|{name}")
    tmp = CACHE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    os.replace(tmp, CACHE)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
