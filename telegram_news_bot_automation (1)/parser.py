import xml.etree.ElementTree as ET
import re
import urllib.request
import urllib.parse
from typing import List, Dict, Optional

def clean_html(raw_html: str) -> str:
    """Видаляє HTML-теги та зайві пробіли з тексту."""
    if not raw_html:
        return ""
    clean = re.sub(r"<[^>]+>", " ", raw_html)
    clean = re.sub(r"&nbsp;|&quot;|&apos;|&amp;|&lt;|&gt;", " ", clean)
    clean = re.sub(r"\s+", " ", clean).strip()
    return clean

def extract_image_url(item_elem: ET.Element) -> Optional[str]:
    """Витягує URL зображення з тегів enclosure або media:content."""
    # Перевірка <enclosure type="image/..." url="..."/>
    enclosure = item_elem.find("enclosure")
    if enclosure is not None and "image" in enclosure.attrib.get("type", ""):
        return enclosure.attrib.get("url")
    
    # Перевірка media namespace
    for child in item_elem:
        if child.tag.endswith("content") or child.tag.endswith("thumbnail"):
            url = child.attrib.get("url")
            if url:
                return url
                
    # Перевірка опису на наявність <img> тегу
    description = item_elem.find("description")
    if description is not None and description.text:
        img_match = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', description.text)
        if img_match:
            return img_match.group(1)
            
    return None

def fetch_rss_feed(feed_url: str) -> List[Dict]:
    """Завантажує та розбирає RSS або Atom потік."""
    articles = []
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        req = urllib.request.Request(feed_url, headers=headers)
        with urllib.request.urlopen(req, timeout=15) as response:
            xml_data = response.read()
            
        root = ET.fromstring(xml_data)
        
        # Обробка стандартного RSS (channel -> item)
        channel = root.find("channel")
        items = channel.findall("item") if channel is not None else root.findall(".//item")
        
        # Якщо це Atom (feed -> entry)
        if not items:
            items = root.findall(".//{http://www.w3.org/2005/Atom}entry") or root.findall(".//entry")

        for item in items:
            title_elem = item.find("title") or item.find("{http://www.w3.org/2005/Atom}title")
            title = clean_html(title_elem.text) if title_elem is not None and title_elem.text else ""
            
            link_elem = item.find("link") or item.find("{http://www.w3.org/2005/Atom}link")
            link = ""
            if link_elem is not None:
                link = link_elem.attrib.get("href", "") or link_elem.text or ""
                
            desc_elem = item.find("description") or item.find("summary") or item.find("{http://www.w3.org/2005/Atom}summary")
            content = clean_html(desc_elem.text) if desc_elem is not None and desc_elem.text else ""
            
            image_url = extract_image_url(item)
            
            if title and (content or link):
                articles.append({
                    "title": title,
                    "content": content if content else title,
                    "link": link,
                    "image_url": image_url,
                    "source_feed": feed_url
                })
    except Exception as e:
        print(f"Помилка при читанні RSS {feed_url}: {e}")
        
    return articles
