import os
import threading
import time
from datetime import datetime, timezone, timedelta

import requests
from bs4 import BeautifulSoup
from flask import Flask

app = Flask(__name__)

WEBHOOK_URL = os.environ.get("WEBHOOK_URL")
CHECK_EVERY = 300                    # co ile sekund sprawdzać stronę
MAX_AGE = timedelta(hours=1)         # wysyłaj tylko artykuły nie starsze niż to
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}

seen = set()
first_run = True


def parse_articles(html):
    """Zwraca listę (link, tytuł, data publikacji lub None)."""
    soup = BeautifulSoup(html, "html.parser")
    articles = []
    for a in soup.select("a.wp-teaser-link[href]"):
        href = a["href"]
        if href.startswith("/"):
            href = "https://www.pudelek.pl" + href

        title_el = a.select_one(".wp-teaser-title")
        title = title_el.get_text(" ", strip=True) if title_el else href

        published = None
        time_el = a.select_one("time[datetime]")
        if time_el:
            try:
                published = datetime.fromisoformat(
                    time_el["datetime"].replace("Z", "+00:00")
                )
            except ValueError:
                pass

        articles.append((href, title, published))
    return articles


def send_to_discord(title, href):
    payload = {"content": f"🆕 **{title}**\n{href}"}
    for _ in range(3):
        r = requests.post(WEBHOOK_URL, json=payload, timeout=30)
        if r.status_code == 429:  # Discord prosi o chwilę przerwy
            time.sleep(float(r.json().get("retry_after", 2)) + 0.5)
            continue
        r.raise_for_status()
        return


def check_pudelek():
    global first_run
    while True:
        try:
            r = requests.get("https://www.pudelek.pl/", headers=HEADERS, timeout=30)
            r.raise_for_status()
            articles = parse_articles(r.text)
            print(f"Znaleziono {len(articles)} artykułów", flush=True)

            now = datetime.now(timezone.utc)
            # od najstarszego do najnowszego, żeby na Discordzie były po kolei
            for href, title, published in reversed(articles):
                if href in seen:
                    continue
                seen.add(href)

                if first_run:
                    continue  # pierwszy obieg tylko zapamiętuje

                fresh = published is not None and now - published < MAX_AGE
                if fresh:
                    send_to_discord(title, href)
                    print(f"Wysłano: {title}", flush=True)
                    time.sleep(1)
                else:
                    print(f"Pominięto (stary lub bez daty): {title}", flush=True)

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
