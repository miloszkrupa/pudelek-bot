import os
import threading
import time
from bs4 import BeautifulSoup
from flask import Flask
import requests

app = Flask(__name__)
WEBHOOK_URL = os.environ.get("WEBHOOK_URL")
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
}
seen = set()
first_run = True


def check_pudelek():
    global first_run
    while True:
        try:
            r = requests.get("https://www.pudelek.pl/", headers=HEADERS, timeout=30)
            soup = BeautifulSoup(r.text, "html.parser")

            articles = []
            for a in soup.select("a.wp-teaser-link"):
                href = a["href"]
                title_el = a.select_one(".wp-teaser-title")
                title = title_el.get_text(strip=True) if title_el else href
                articles.append((href, title))

            print(f"Znaleziono {len(articles)} artykułów")

            for href, title in reversed(articles):
                if href in seen:
                    continue
                seen.add(href)
                if not first_run:
                    requests.post(
                        WEBHOOK_URL,
                        json={"content": f"🆕 **{title}**\n{href}"},
                        timeout=30,
                    )
                    time.sleep(1)
            first_run = False
        except Exception as e:
            print(f"Błąd: {e}")
        time.sleep(300)


threading.Thread(target=check_pudelek, daemon=True).start()


@app.route("/")
def home():
    return "Bot Pudelek działa!"


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
