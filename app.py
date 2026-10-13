import os
import re
import threading
import time
from datetime import datetime, timezone, timedelta

import requests
from bs4 import BeautifulSoup
from flask import Flask

app = Flask(__name__)

WEBHOOK_URL = os.environ.get("WEBHOOK_URL")
TREND_WEBHOOK_URL = (
    os.environ.get("TREND_WEBHOOK_URL")
    or os.environ.get("HOT_WEBHOOK_URL")
    or os.environ.get("ALERT_WEBHOOK_URL")
    or WEBHOOK_URL
)

BASE = "https://www.pudelek.pl"
CHECK_EVERY = 300                        # co ile sekund sprawdzać stronę
MAX_AGE = timedelta(hours=1)             # nowe artykuły: wysyłaj tylko świeższe niż to

THRESHOLDS = [50, 100, 200, 500]         # progi komentarzy
VELOCITY_WINDOW = timedelta(minutes=30)
VELOCITY_MIN_SPAN = timedelta(minutes=15)
VELOCITY_MIN_GAIN = 25                   # tyle nowych komentarzy w oknie = alert tempa
VELOCITY_COOLDOWN = timedelta(hours=1)
KEEP = timedelta(hours=24)

RANK_EVERY = timedelta(hours=1)          # jak często ranking
RANK_WINDOW = timedelta(hours=1)         # z jakiego okresu liczyć przyrost
RANK_MIN_SPAN = timedelta(minutes=20)    # minimalna długość obserwacji, żeby artykuł wszedł do rankingu
RANK_MIN_GAIN = 5                        # minimalny przyrost komentarzy
RANK_SIZE = 5

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}

SECTIONS = {
    "stream": "a.wp-teaser-link[href]",
    "hero": "a.wp-rwd-teaser-glonews[href], a.wp-teaser-glonews[href]",
}

seen = set()
hero_seen = set()
tracked = {}
last_rank = None
first_run = True


def parse_articles(html):
    soup = BeautifulSoup(html, "html.parser")
    found = {}
    for section, selector in SECTIONS.items():
        for a in soup.select(selector):
            href = a["href"]
            if href.startswith("/"):
                href = BASE + href
            item = found.setdefault(
                href,
                {"href": href, "title": href, "comments": None,
                 "published": None, "sections": set()},
            )
            item["sections"].add(section)

            title_el = a.select_one(".wp-teaser-title")
            if title_el:
                item["title"] = title_el.get_text(" ", strip=True)

            c_el = a.select_one(".wp-teaser-comments")
            if c_el:
                digits = re.sub(r"\D", "", c_el.get_text())
                if digits:
                    item["comments"] = max(int(digits), item["comments"] or 0)

            t_el = a.select_one("time[datetime]")
            if t_el and item["published"] is None:
                try:
                    item["published"] = datetime.fromisoformat(
                        t_el["datetime"].replace("Z", "+00:00")
                    )
                except ValueError:
                    pass
    return list(found.values())


def post_discord(content, webhook=None):
    webhook = webhook or WEBHOOK_URL
    for _ in range(3):
        r = requests.post(webhook, json={"content": content[:1900]}, timeout=30)
        if r.status_code == 429:
            time.sleep(float(r.json().get("retry_after", 2)) + 0.5)
            continue
        r.raise_for_status()
        return


def trend_post(content):
    post_discord(content, TREND_WEBHOOK_URL)
    time.sleep(1)


def track_interest(items, now, initial):
    for it in items:
        c = it["comments"]
        if c is None:
            continue

        rec = tracked.setdefault(
            it["href"],
            {"title": it["title"], "points": [], "alerted": set(),
             "last_velocity": None, "last_seen": now},
        )
        rec["title"] = it["title"]
        rec["last_seen"] = now
        rec["points"].append((now, c))
        rec["points"] = [p for p in rec["points"] if now - p[0] <= timedelta(hours=3)]

        if initial:
            rec["alerted"].update(t for t in THRESHOLDS if c >= t)
            continue

        crossed = [t for t in THRESHOLDS if c >= t and t not in rec["alerted"]]
        if crossed:
            rec["alerted"].update(crossed)
            trend_post(f"🔥 **{it['title']}**\n{c} komentarzy (próg {max(crossed)})\n{it['href']}")

        in_window = [p for p in rec["points"] if now - p[0] <= VELOCITY_WINDOW]
        if in_window:
            base_time, base_c = in_window[0]
            span = now - base_time
            gain = c - base_c
            cooled = rec["last_velocity"] is None or now - rec["last_velocity"] >= VELOCITY_COOLDOWN
            if span >= VELOCITY_MIN_SPAN and gain >= VELOCITY_MIN_GAIN and cooled:
                rec["last_velocity"] = now
                trend_post(f"📈 **{it['title']}**\n+{gain} komentarzy w {int(span.total_seconds() // 60)} min (razem {c})\n{it['href']}")

    for href in [h for h, r in tracked.items() if now - r["last_seen"] > KEEP]:
        del tracked[href]


def hourly_ranking(now, initial):
    global last_rank
    if initial:
        last_rank = now
        return
    if last_rank is not None and now - last_rank < RANK_EVERY:
        return

    rows = []
    for href, rec in tracked.items():
        if rec["last_seen"] != now:  # tylko artykuły, które są teraz na stronie
            continue
        window = [p for p in rec["points"] if now - p[0] <= RANK_WINDOW]
        if not window:
            continue
        base_time, base_c = window[0]
        gain = window[-1][1] - base_c
        if now - base_time >= RANK_MIN_SPAN and gain >= RANK_MIN_GAIN:
            rows.append((gain, window[-1][1], rec["title"], href))

    if not rows:
        return  # za mało danych albo nic się nie rusza; spróbuje przy następnym obiegu

    last_rank = now
    rows.sort(reverse=True)
    lines = ["🏆 **Trenduje (przyrost komentarzy w ostatniej godzinie)**"]
    for i, (gain, total, title, href) in enumerate(rows[:RANK_SIZE], 1):
        lines.append(f"{i}. +{gain} (razem {total}) **{title[:100]}**\n{href}")
    trend_post("\n".join(lines))


def check_pudelek():
    global first_run
    while True:
        try:
            r = requests.get(BASE + "/", headers=HEADERS, timeout=30)
            r.raise_for_status()
            items = parse_articles(r.text)
            now = datetime.now(timezone.utc)
            with_counter = sum(1 for i in items if i["comments"] is not None)
            print(f"Znaleziono {len(items)} artykułów, z licznikiem komentarzy: {with_counter}", flush=True)

            # --- nowe artykuły z listy DZIŚ ---
            stream = [i for i in items if "stream" in i["sections"]]
            stream.sort(key=lambda i: i["published"] or now)
            for it in stream:
                if it["href"] in seen:
                    continue
                if first_run:
                    seen.add(it["href"])
                    continue
                fresh = it["published"] is not None and now - it["published"] < MAX_AGE
                if fresh:
                    post_discord(f"🆕 **{it['title']}**\n{it['href']}")
                    print(f"Wysłano: {it['title']}", flush=True)
                    time.sleep(1)
                else:
                    print(f"Pominięto (stary lub bez daty): {it['title']}", flush=True)
                seen.add(it["href"])

            # --- wskoczył na główną ---
            for it in items:
                if "hero" in it["sections"] and it["href"] not in hero_seen:
                    if not first_run:
                        c = f" ({it['comments']} komentarzy)" if it["comments"] is not None else ""
                        trend_post(f"⭐ **Na głównej:** {it['title']}{c}\n{it['href']}")
                        print(f"Główna: {it['title']}", flush=True)
                    hero_seen.add(it["href"])

            # --- zainteresowanie i ranking ---
            track_interest(items, now, initial=first_run)
            hourly_ranking(now, initial=first_run)

            first_run = False
        except Exception as e:
            print(f"Błąd: {e}", flush=True)
        time.sleep(CHECK_EVERY)


threading.Thread(target=check_pudelek, daemon=True).start()


@app.route("/")
def home():
    return "Bot Pudelek działa!"


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
