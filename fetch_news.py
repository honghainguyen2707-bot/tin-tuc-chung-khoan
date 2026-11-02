#!/usr/bin/env python3
"""Tổng hợp RSS tài chính cho website Hồng Hải; chỉ lưu tóm tắt và liên kết nguồn."""
import datetime as dt
import email.utils
import html
import json
import logging
import re
import time
import unicodedata
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

BASE = Path(__file__).resolve().parent
OUTPUT = BASE / 'news.json'
MAX_ITEMS = 300
MAX_AGE_DAYS = 10
FEEDS = [
    ('CafeF - Chứng khoán', 'https://cafef.vn/thi-truong-chung-khoan.rss'),
    ('CafeF - Ngân hàng', 'https://cafef.vn/tai-chinh-ngan-hang.rss'),
    ('Vietstock - Chứng khoán', 'https://vietstock.vn/144/chung-khoan.rss'),
    ('Vietstock - Cổ phiếu', 'https://vietstock.vn/830/chung-khoan/co-phieu.rss'),
    ('Vietstock - Doanh nghiệp', 'https://vietstock.vn/733/doanh-nghiep.rss'),
    ('VnEconomy - Chứng khoán', 'https://vneconomy.vn/chung-khoan.rss'),
    ('VnEconomy - Kinh tế thế giới', 'https://vneconomy.vn/kinh-te-the-gioi.rss'),
    ('Báo Đầu tư - Đầu tư tài chính', 'https://baodautu.vn/dau-tu-tai-chinh.rss'),
    ('Báo Đầu tư - Ngân hàng', 'https://baodautu.vn/ngan-hang--bao-hiem.rss'),
    ('VnExpress - Kinh doanh', 'https://vnexpress.net/rss/kinh-doanh.rss'),
]
KEYWORDS = (
    'chung khoan', 'co phieu', 'vn-index', 'vnindex', 'hose', 'hnx', 'upcom',
    'niem yet', 'co tuc', 'trai phieu', 'lai suat', 'ty gia', 'ngan hang',
    'tin dung', 'no xau', 'loi nhuan', 'ket qua kinh doanh', 'bao cao tai chinh',
    'doanh thu', 'phat hanh', 'tang von', 'thoai von', 'mua ban sap nhap',
    'dau tu cong', 'gdp', 'lam phat', 'xuat khau', 'nhap khau', 'fed',
    'dxy', 'dow jones', 'nasdaq', 's&p 500', 'pho wall', 'gia dau',
    'dau tho', 'gia vang', 'gia thep', 'hang hoa', 'bat dong san',
    'doanh nghiep', 'tai chinh', 'thi truong', 'kinh te', 'quy dau tu',
    'khoi ngoai', 'thanh khoan', 'dong tien', 'du an', 'ngan sach',
)
STRONG = ('chung khoan', 'co phieu', 'vn-index', 'vnindex', 'hose', 'hnx', 'upcom',
          'co tuc', 'trai phieu', 'lai suat', 'ty gia', 'ngan hang', 'tin dung',
          'loi nhuan', 'bao cao tai chinh', 'dau tu cong', 'fed', 'gia dau',
          'gia vang', 'gdp', 'lam phat', 'khoi ngoai', 'thanh khoan')

logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')


def normalize(s):
    s = unicodedata.normalize('NFD', s or '')
    s = ''.join(c for c in s if unicodedata.category(c) != 'Mn')
    return s.replace('đ', 'd').replace('Đ', 'D').lower()


def strip_html(s):
    s = re.sub(r'<[^>]+>', ' ', html.unescape(s or ''))
    return re.sub(r'\s+', ' ', s).strip()


def child_text(element, names):
    for c in element:
        if c.tag.rsplit('}', 1)[-1].lower() in names:
            text = ''.join(c.itertext()).strip()
            if text:
                return text
    return ''


def parse_date(value):
    if not value:
        return None
    try:
        result = email.utils.parsedate_to_datetime(value)
        if result:
            return result.replace(tzinfo=dt.timezone.utc) if result.tzinfo is None else result
    except (TypeError, ValueError, IndexError):
        pass
    try:
        result = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
        return result.replace(tzinfo=dt.timezone.utc) if result.tzinfo is None else result
    except ValueError:
        return None


def canonical_url(url):
    parts = urlsplit(url.strip())
    if parts.scheme not in ('https', 'http') or not parts.netloc:
        return ''
    return urlunsplit((parts.scheme, parts.netloc.lower(), parts.path, '', ''))


def relevant(title, description, source):
    text = normalize(title + ' ' + description)
    if any(word in text for word in STRONG):
        return True
    return any(word in text for word in KEYWORDS) and any(
        tag in source for tag in ('Chứng khoán', 'Cổ phiếu', 'Doanh nghiệp', 'Đầu tư tài chính')
    )


def clean_xml_payload(payload):
    """Loại BOM/khoảng trắng đầu tài liệu; không tự ý cắt bỏ HTML hoặc XML hỏng."""
    if not isinstance(payload, bytes) or not payload:
        raise ValueError('Phản hồi RSS rỗng')
    payload = payload.removeprefix(b'\xef\xbb\xbf').lstrip(b' \t\r\n')
    # Một số server đặt BOM hoặc khoảng trắng trước khai báo XML.
    payload = payload.removeprefix(b'\xef\xbb\xbf').lstrip(b' \t\r\n')
    head = payload[:512].lower()
    if (head.startswith(b'<!doctype html') or head.startswith(b'<html')
            or b'<html' in head[:150]):
        raise ValueError('Máy chủ trả về HTML thay vì RSS/XML; cần kiểm tra URL hoặc chặn truy cập')
    if not payload.startswith((b'<?xml', b'<rss', b'<feed', b'<rdf:', b'<RDF:')):
        raise ValueError('Phản hồi không bắt đầu bằng khai báo XML hoặc RSS hợp lệ')
    return payload


def parse_feed(xml_bytes, source):
    xml_bytes = clean_xml_payload(xml_bytes)
    root = ET.fromstring(xml_bytes)
    root_tag = root.tag.rsplit('}', 1)[-1].lower()
    if root_tag not in ('rss', 'feed', 'rdf'):
        raise ValueError(f'Gốc XML không phải RSS/Atom: {root_tag}')
    entries = [el for el in root.iter()
               if el.tag.rsplit('}', 1)[-1].lower() in ('item', 'entry')]
    result = []
    now = time.time()
    for entry in entries:
        title = strip_html(child_text(entry, {'title'}))
        description = strip_html(child_text(entry, {'description', 'summary', 'content', 'encoded'}))[:450]
        link = child_text(entry, {'link'})
        if not link:
            for c in entry:
                if c.tag.rsplit('}', 1)[-1].lower() == 'link' and c.attrib.get('href'):
                    link = c.attrib['href']
                    break
        link = canonical_url(link)
        date = parse_date(child_text(entry, {'pubdate', 'published', 'updated', 'date'}))
        if not title or not link or not relevant(title, description, source) or not date:
            continue
        stamp = date.timestamp()
        if stamp > now + 3600 or stamp < now - MAX_AGE_DAYS * 86400:
            continue
        result.append({
            'title': title[:250], 'description': description,
            'link': link, 'source': source,
            'published': date.strftime('%a, %d %b %Y %H:%M:%S %z'),
            'timestamp': stamp,
        })
    return result


def read_previous():
    try:
        data = json.loads(OUTPUT.read_text(encoding='utf-8'))
        return data if isinstance(data, dict) and isinstance(data.get('items'), list) else {'items': []}
    except (OSError, ValueError):
        return {'items': []}


def main():
    previous = read_previous()
    collected = []
    success = 0
    for source, url in FEEDS:
        try:
            req = urllib.request.Request(url, headers={
                'User-Agent': 'Mozilla/5.0 (compatible; HongHaiNewsRSS/1.0; +https://github.com/honghainguyen2707-bot/tin-tuc-chung-khoan)',
                'Accept': 'application/rss+xml, application/xml, text/xml, */*',
            })
            with urllib.request.urlopen(req, timeout=16) as response:
                payload = response.read(3_000_000)
            found = parse_feed(payload, source)
            logging.info('%s: %s bài phù hợp', source, len(found))
            collected.extend(found)
            success += 1
        except Exception as exc:
            logging.warning('%s: %s', source, exc)
    combined = collected + previous['items']
    dedup = {}
    cutoff = time.time() - MAX_AGE_DAYS * 86400
    for item in combined:
        try:
            url = canonical_url(str(item.get('link', '')))
            stamp = float(item.get('timestamp', 0))
            if not url or stamp < cutoff or stamp > time.time() + 3600:
                continue
            if url not in dedup or stamp > float(dedup[url]['timestamp']):
                dedup[url] = item
        except (TypeError, ValueError, KeyError, AttributeError):
            continue
    ordered = sorted(dedup.values(), key=lambda x: float(x['timestamp']), reverse=True)[:MAX_ITEMS]
    if not ordered:
        logging.warning('Không có bài hợp lệ; giữ nguyên news.json')
        return
    OUTPUT.write_text(json.dumps({'items': ordered}, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    logging.info('Hoàn tất: %d nguồn đọc được, %d tin lưu', success, len(ordered))


if __name__ == '__main__':
    main()
