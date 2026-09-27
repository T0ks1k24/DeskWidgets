#!/usr/bin/env python3
"""Weather widget data: fetches wttr.in and writes a key=value cache.

Usage: fetch.py [LOCATION] [TITLE] [CACHE] [LANG]
  LOCATION  what wttr.in understands (city name in Latin, "Lviv", "London", "~Eiffel tower")
  TITLE     name shown in the widget
  CACHE     output file
  LANG      uk or en (condition descriptions)
On any network/parse error the old cache is kept untouched.
"""
import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta

LOC = sys.argv[1] if len(sys.argv) > 1 else "Lutsk"
CITY = sys.argv[2] if len(sys.argv) > 2 else "Луцьк"
CACHE = sys.argv[3] if len(sys.argv) > 3 else os.path.expanduser("~/.cache/deskwidgets-weather")
LANG = sys.argv[4] if len(sys.argv) > 4 else "uk"
HOURLY_SLOTS = 5  # forecast columns after "Now"
DAYS = 3


def parse_clock(day: str, hhmm: str) -> datetime:
    return datetime.strptime(f"{day} {hhmm}", "%Y-%m-%d %I:%M %p")


def main() -> None:
    url = f"https://wttr.in/{urllib.parse.quote(LOC)}?format=j1&lang={LANG}"
    req = urllib.request.Request(url, headers={"User-Agent": "curl/8"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.load(resp)

    now = datetime.now()
    cur = data["current_condition"][0]
    today = data["weather"][0]

    def is_day(when: datetime) -> bool:
        for day in data["weather"]:
            if day["date"] == when.strftime("%Y-%m-%d"):
                astro = day["astronomy"][0]
                try:
                    rise = parse_clock(day["date"], astro["sunrise"])
                    sset = parse_clock(day["date"], astro["sunset"])
                except ValueError:  # "No sunrise" etc.
                    break
                return rise <= when < sset
        return 7 <= when.hour < 19

    hourly = []
    for day in data["weather"]:
        base = datetime.strptime(day["date"], "%Y-%m-%d")
        for h in day["hourly"]:
            when = base + timedelta(hours=int(h["time"]) // 100)
            if when > now:
                hourly.append((when, h["weatherCode"], h["tempC"], h["chanceofrain"]))
    hourly = hourly[:HOURLY_SLOTS]

    desc = (cur.get(f"lang_{LANG}") or [{}])[0].get("value") or cur["weatherDesc"][0]["value"]
    lines = [
        f"city={CITY}",
        f"temp={cur['temp_C']}",
        f"feels={cur['FeelsLikeC']}",
        f"code={cur['weatherCode']}",
        f"desc={desc}",
        f"hum={cur['humidity']}",
        f"wind={cur['windspeedKmph']}",
        f"hi={today['maxtempC']}",
        f"lo={today['mintempC']}",
        f"day={1 if is_day(now) else 0}",
        f"updated={now:%H:%M}",
    ]
    for i, (when, code, temp, rain) in enumerate(hourly, 1):
        lines.append(f"h{i}={when:%H}:00|{code}|{temp}|{1 if is_day(when) else 0}|{rain}")
    # daily: date|midday code|max|min|highest chance of rain
    for i, day in enumerate(data["weather"][:DAYS], 1):
        hours = day["hourly"]
        noon = hours[len(hours) // 2]
        rain = max(int(h.get("chanceofrain", 0)) for h in hours)
        lines.append(f"d{i}={day['date']}|{noon['weatherCode']}|{day['maxtempC']}|{day['mintempC']}|{rain}")

    tmp = CACHE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    os.replace(tmp, CACHE)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
