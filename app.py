import os
import threading
import time
from bs4 import BeautifulSoup
from flask import Flask
import requests

app = Flask(__name__)
WEBHOOK_URL = os.environ.get("WEBHOOK_URL")
last_article_url = None


def check_pudelek():
  global last_article_url
  while True:
    try:
      headers = {
          "User-Agent": (
              "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
          )
      }
      response = requests.get("https://www.pudelek.pl/", headers=headers)
      soup = BeautifulSoup(response.text, "html.parser")

      for link in soup.find_all("a", href=True):
        href = link["href"]
        if href.startswith("/") and len(href) > 15 and href.count("-") > 3:
          current_url = "https://www.pudelek.pl" + href
          if last_article_url is None:
            last_article_url = current_url
          elif current_url != last_article_url:
            requests.post(
                WEBHOOK_URL,
                json={
                    "content": (
                        "🚨 **Nowy artykuł na Pudelku!**\n" f"{current_url}"
                    )
                },
            )
            last_article_url = current_url
          break
    except Exception as e:
      print(f"Błąd: {e}")
    time.sleep(60)


threading.Thread(target=check_pudelek, daemon=True).start()


@app.route("/")
def home():
  return "Bot Pudelek działa!"


if __name__ == "__main__":
  app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
