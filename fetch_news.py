
import json
import re
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from pathlib import Path

# Nguồn RSS công khai. Có thể bổ sung nguồn khác sau.
SOURCES = {
    "VnExpress - Kinh doanh": "https://vnexpress.net/rss/kinh-doanh.rss",
    "VnExpress - Thế giới": "https://vnexpress.net/rss/the-gioi.rss",
    "VnExpress - Tin mới": "https://vnexpress.net/rss/tin-moi-nhat.rss",
}

KEYWORDS = (
    "chứng khoán", "cổ phiếu", "vn-index", "vnindex",
    "hose", "hnx", "upcom", "ngân hàng", "lãi suất",
    "tỷ giá", "trái phiếu", "doanh nghiệp", "đầu tư",
    "fed", "nasdaq", "dow jones", "s&p 500",
    "giá dầu", "dầu thô", "giá vàng", "kinh tế",
    "xuất khẩu", "bất động sản", "lợi nhuận",
    "cổ tức", "phát hành", "thoái vốn"
)

DATA_FILE = Path("news.json")


def clean(value):
    value = re.sub(r"<[^>]+>", " ", value or "")
    return re.sub(r"\s+", " ", unescape(value)).strip()


def fetch_feed(source, url):
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 (compatible; NewsReader/1.0)"}
    )

    with urllib.request.urlopen(request, timeout=20) as response:
        content = response.read()

    root = ET.fromstring(content)
    articles = []

    for item in root.findall(".//item"):
        title = clean(item.findtext("title"))
        description = clean(item.findtext("description"))
        link = (item.findtext("link") or "").strip()
        published = (item.findtext("pubDate") or "").strip()

        if not title or not link:
            continue

        combined = (title + " " + description).lower()

        if not any(word in combined for word in KEYWORDS):
            continue

        try:
            date = parsedate_to_datetime(published)
            if date.tzinfo is None:
                date = date.replace(tzinfo=timezone.utc)
            timestamp = date.timestamp()
        except (ValueError, TypeError, OverflowError):
            timestamp = 0

        articles.append({
            "title": title,
            "description": description[:400],
            "link": link,
            "source": source,
            "published": published,
            "timestamp": timestamp
        })

    return articles


def main():
    # Giữ tin cũ khi nguồn RSS tạm thời gặp lỗi.
    existing = []

    if DATA_FILE.exists():
        try:
            old_data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
            existing = (
                old_data if isinstance(old_data, list)
                else old_data.get("items", [])
            )
        except (ValueError, OSError, AttributeError):
            pass

    all_articles = list(existing)
    successful = 0

    for source, url in SOURCES.items():
        try:
            articles = fetch_feed(source, url)
            all_articles.extend(articles)
            successful += 1
            print(f"{source}: {len(articles)} tin")
        except Exception as error:
            print(f"Lỗi nguồn {source}: {error}")

    if successful == 0:
        print("Không có nguồn RSS nào truy cập thành công.")
        return

    unique = {}

    for article in all_articles:
        link = article.get("link") or article.get("url")
        if link:
            unique[link] = article

    results = sorted(
        unique.values(),
        key=lambda x: x.get("timestamp", 0),
        reverse=True
    )[:200]

    DATA_FILE.write_text(
        json.dumps(
            {"items": results},
            ensure_ascii=False,
            indent=2
        ),
        encoding="utf-8"
    )

    print(f"Đã lưu {len(results)} tin.")


if __name__ == "__main__":
    main()
