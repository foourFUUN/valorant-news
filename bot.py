"""
Valorant News — envia notícias do THESPIKE.GG (BR) e do VLR.gg para um webhook do Discord.

Roda uma vez por execução (pensado para o GitHub Actions agendado):
  1. Lê a listagem de notícias de cada site
  2. Compara com o que já foi enviado (sent.json)
  3. Para cada notícia nova, abre a página e pega título, resumo, imagem e data
  4. Envia um embed vermelho pelo webhook
"""

import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

import discord
import requests
from bs4 import BeautifulSoup

# ---------------------------------------------------------------- config
WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
AVATAR_URL = os.environ.get("AVATAR_URL", "").strip() or None
BOT_NAME = "Demon Vava News"
COLOR = 0xFF4655  # vermelho do Valorant

STATE_FILE = Path(__file__).with_name("sent.json")
MAX_POSTS_PER_RUN = int(os.environ.get("MAX_POSTS_PER_RUN", "10"))  # por site
FIRST_RUN_POSTS = int(os.environ.get("FIRST_RUN_POSTS", "1"))      # por site, na 1ª execução
KEEP_IDS = 300
SEND_DELAY = 2  # segundos entre envios (evita rate limit do webhook)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/129.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
}

SOURCES = {
    "thespike": {
        "name": "THESPIKE.GG",
        "home": "https://www.thespike.gg/br",
        "list_url": "https://www.thespike.gg/br/valorant/news",
        "host": "www.thespike.gg",
        "pattern": re.compile(r"^/br/valorant/news/[^/]+/(\d+)/?$"),
        "selector": None,
        "body_selector": "article img",
        "generic_images": ("og-img.png",),
    },
    "vlr": {
        "name": "VLR.gg",
        "home": "https://www.vlr.gg",
        "list_url": "https://www.vlr.gg/news",
        "host": "www.vlr.gg",
        "pattern": re.compile(r"^/(\d+)/[^/]+/?$"),
        "selector": "a.wf-module-item",
        "body_selector": ".article-body img",
        "generic_images": ("card.png",),
    },
}


# ---------------------------------------------------------------- estado
def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            print("sent.json corrompido, começando do zero")
    return {}


def save_state(state: dict) -> None:
    for key in state:
        state[key] = state[key][-KEEP_IDS:]
    STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


# ---------------------------------------------------------------- scraping
def get_soup(session: requests.Session, url: str) -> BeautifulSoup:
    r = session.get(url, timeout=20)
    r.raise_for_status()
    return BeautifulSoup(r.text, "html.parser")


def fetch_list(session: requests.Session, src: dict) -> list[tuple[str, str]]:
    """Retorna [(id, url)] na ordem da página (mais nova primeiro)."""
    soup = get_soup(session, src["list_url"])
    anchors = soup.select(src["selector"]) if src["selector"] else []
    if not anchors:
        anchors = soup.find_all("a", href=True)

    items, seen = [], set()
    for a in anchors:
        url = urljoin(src["list_url"], a.get("href", ""))
        parsed = urlparse(url)
        if parsed.netloc != src["host"]:
            continue
        m = src["pattern"].match(parsed.path)
        if not m or m.group(1) in seen:
            continue
        seen.add(m.group(1))
        items.append((m.group(1), f"https://{parsed.netloc}{parsed.path}"))
    return items


def meta(soup: BeautifulSoup, *keys: str) -> str | None:
    for k in keys:
        tag = soup.find("meta", attrs={"property": k}) or soup.find("meta", attrs={"name": k})
        if tag and tag.get("content"):
            return tag["content"].strip()
    return None


DATE_KEYS = re.compile(
    r'"(?:datePublished|publishedAt|published_at|publishDate|createdAt|created_at)"\s*:\s*"([^"]+)"'
)


def to_utc(raw: str) -> datetime | None:
    raw = raw.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def parse_date(soup: BeautifulSoup) -> datetime | None:
    # 1) meta tags padrão
    raw = meta(soup, "article:published_time", "og:published_time", "date", "pubdate")
    if raw and (dt := to_utc(raw)):
        return dt
    # 2) VLR.gg: <span data-utc-ts="2026-10-16 22:45:00">
    tag = soup.find(attrs={"data-utc-ts": True})
    if tag and (dt := to_utc(tag["data-utc-ts"])):
        return dt
    # 3) JSON embutido (JSON-LD, __NEXT_DATA__ etc.)
    for s in soup.find_all("script"):
        m = DATE_KEYS.search(s.string or "")
        if m and (dt := to_utc(m.group(1))):
            return dt
    # 4) <time datetime="...">
    t = soup.find("time", attrs={"datetime": True})
    if t:
        return to_utc(t["datetime"])
    return None


def clean_title(title: str) -> str:
    return re.sub(r"\s*[|–-]\s*(VLR\.gg|THESPIKE(\.GG)?)\s*$", "", title, flags=re.I).strip()


def fetch_article(session: requests.Session, src: dict, url: str) -> dict:
    soup = get_soup(session, url)

    title = meta(soup, "og:title", "twitter:title") or (soup.title.string if soup.title else url)
    summary = meta(soup, "og:description", "description", "twitter:description") or ""

    image = meta(soup, "og:image", "twitter:image")
    if image and image.endswith(src["generic_images"]):
        image = None  # imagem padrão do site, não da notícia
    if not image:
        img = soup.select_one(src["body_selector"])
        if img and img.get("src"):
            image = img["src"]
    if image:
        image = urljoin(url, image)

    return {
        "title": clean_title(title),
        "summary": summary[:350] + ("…" if len(summary) > 350 else ""),
        "image": image,
        "url": url,
        "published": parse_date(soup) or datetime.now(timezone.utc),
        "source": src["name"],
        "source_home": src["home"],
    }


# ---------------------------------------------------------------- discord
def build_embed(n: dict) -> discord.Embed:
    description = None
    if n["summary"]:
        description = "**Resumo:**\n> " + n["summary"].replace("\n", "\n> ")

    embed = discord.Embed(
        title=n["title"][:256],
        url=n["url"],
        description=description,
        color=COLOR,
        timestamp=n["published"],
    )
    embed.set_author(name=f"{BOT_NAME} • News Digest", icon_url=AVATAR_URL)

    ts = int(n["published"].timestamp())
    embed.add_field(name="📰 Fonte", value=f"[{n['source']}]({n['source_home']})", inline=True)
    embed.add_field(name="🕒 Publicado", value=f"<t:{ts}:R>", inline=True)
    embed.add_field(name="🔗 Link", value=f"[Abrir notícia]({n['url']})", inline=True)

    if n["image"]:
        embed.set_image(url=n["image"])
    embed.set_footer(text=BOT_NAME, icon_url=AVATAR_URL)
    return embed


# ---------------------------------------------------------------- main
def main() -> None:
    if not WEBHOOK_URL:
        sys.exit("Defina a variável DISCORD_WEBHOOK_URL")

    state = load_state()
    webhook = discord.SyncWebhook.from_url(WEBHOOK_URL)
    session = requests.Session()
    session.headers.update(HEADERS)

    pending: list[tuple[str, str, str]] = []
    for key, src in SOURCES.items():
        try:
            items = fetch_list(session, src)
        except Exception as e:  # noqa: BLE001
            print(f"[{key}] erro ao ler a listagem: {e}")
            continue
        if not items:
            print(f"[{key}] nenhum link encontrado — o layout do site pode ter mudado")
            continue

        first_run = key not in state
        seen = set(state.get(key, []))
        new = [(i, u) for i, u in items if i not in seen]

        if first_run:
            # não inunda o canal: manda só as N mais recentes e marca o resto como visto
            state[key] = [i for i, _ in reversed(new[FIRST_RUN_POSTS:])]
            new = new[:FIRST_RUN_POSTS]

        new = new[:MAX_POSTS_PER_RUN]
        print(f"[{key}] {len(items)} na página, {len(new)} nova(s)")
        pending.extend((key, i, u) for i, u in reversed(new))  # mais antiga primeiro

    try:
        for key, news_id, url in pending:
            src = SOURCES[key]
            try:
                news = fetch_article(session, src, url)
                webhook.send(
                    embed=build_embed(news),
                    username=BOT_NAME,
                    avatar_url=AVATAR_URL,
                    wait=True,
                )
                print(f"[{key}] enviado: {news['title']}")
            except Exception as e:  # noqa: BLE001
                print(f"[{key}] falhou {url}: {e} (tenta de novo na próxima execução)")
                continue
            state.setdefault(key, []).append(news_id)
            time.sleep(SEND_DELAY)
    finally:
        save_state(state)


if __name__ == "__main__":
    main()
