"""Pull a streamer's live link out of websocket text and jQuery data."""

from __future__ import annotations
import json
import re
from typing import Iterable, Optional
from urllib.parse import urlparse

_MEDIA = re.compile(
    r"https?://[^\s\"'<>\\]+(?:m3u8|mpd|mp4|flv)(?:\?[^\s\"'<>\\]*)?",
    re.I,
)
_URL = re.compile(r"https?://[^\s\"'<>\\]+", re.I)
_NAME_KEYS = ("username", "user", "name", "model", "streamer", "room", "slug", "broadcaster", "nickname")
_STATUS_KEYS = ("status", "online", "live", "state", "show", "room_status", "current_show", "is_live")
_URL_KEYS = ("url", "href", "hls", "hls_source", "stream_url", "streamurl", "playlist", "mp4", "link", "room_url")
_ONLINE = {"online", "live", "public", "true", "1", "available", "on"}
_OFFLINE = {"offline", "away", "private", "hidden", "false", "0", "off", "group"}


def _norm(value) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def _token(name: str, text: str) -> bool:
    key = _norm(name)
    if not key or not text:
        return False
    return key in re.sub(r"[^a-z0-9]", "", text.lower()) and re.search(
        rf"(?<![a-z0-9]){re.escape(name.lower())}(?![a-z0-9])",
        text.lower(),
    )


def stream_reached(text: str, url: str) -> bool:
    """True when Whatnot is open on this live link.

    A different live id still on screen is not a success.
    """
    live = (url or "").rstrip("/").rsplit("/", 1)[-1]
    blob = text or ""
    if "com.whatnot_mobile" not in blob or not live:
        return False
    if live in blob:
        return True
    if "/live/" in blob:
        return False
    return "ACTIVITY com.whatnot_mobile" in blob or "mResumedActivity" in blob



def safe_url(url: Optional[str]) -> Optional[str]:
    if not url or not isinstance(url, str):
        return None
    url = url.strip().rstrip("\\,);")
    if any(c in url for c in ("\n", "\r", '"', "'", "`", " ", "|")):
        return None
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return None
    return url


def _flag(value) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    if text in _ONLINE:
        return True
    if text in _OFFLINE:
        return False
    return None


def _from_obj(name: str, obj, found: dict) -> None:
    if isinstance(obj, dict):
        named = None
        for key in _NAME_KEYS:
            if key in obj and _norm(obj.get(key)) == _norm(name):
                named = True
                break
        if named:
            online = None
            url = None
            for key in _STATUS_KEYS:
                if key in obj:
                    online = _flag(obj.get(key))
                    if online is not None:
                        break
            for key in _URL_KEYS:
                url = safe_url(obj.get(key) if isinstance(obj.get(key), str) else None)
                if url:
                    break
            if url is None:
                raw = json.dumps(obj, default=str)
                media = _MEDIA.search(raw)
                if media:
                    url = safe_url(media.group(0))
            if online is None and url and _MEDIA.search(url):
                online = True
            slot = found.setdefault(name, {"online": False, "url": None})
            if online is True:
                slot["online"] = True
                if url:
                    slot["url"] = url
            elif online is False and not slot["online"]:
                slot["online"] = False
            elif url and slot["online"] and not slot["url"]:
                slot["url"] = url
        for value in obj.values():
            _from_obj(name, value, found)
    elif isinstance(obj, list):
        for value in obj:
            _from_obj(name, value, found)


def _from_text(name: str, text: str, found: dict) -> None:
    if not _token(name, text):
        return
    low = text.lower()
    start = 0
    key = name.lower()
    while True:
        at = low.find(key, start)
        if at < 0:
            break
        window = text[max(0, at - 180): at + 320]
        online = None
        if re.search(r"\b(offline|private|away)\b", window, re.I):
            online = False
        elif re.search(r"\b(online|live|public)\b", window, re.I):
            online = True
        media = _MEDIA.search(window)
        url = safe_url(media.group(0)) if media else None
        slot = found.setdefault(name, {"online": False, "url": None})
        if online is True:
            slot["online"] = True
            if url:
                slot["url"] = url
        start = at + len(key)


def read_streams(names: Iterable[str], blobs: Iterable[str], links: Iterable[dict], page_url: str = "") -> dict:
    wanted = [n for n in names if n]
    found: dict = {}
    parsed_page = urlparse(page_url) if page_url else None
    origin = f"{parsed_page.scheme}://{parsed_page.netloc}" if parsed_page and parsed_page.scheme and parsed_page.netloc else ""

    for blob in blobs:
        if not blob:
            continue
        text = blob if isinstance(blob, str) else str(blob)
        try:
            data = json.loads(text)
        except Exception:
            data = None
        for name in wanted:
            if data is not None:
                _from_obj(name, data, found)
            _from_text(name, text[:20000], found)

    for link in links or []:
        text = f"{link.get('text') or ''} {link.get('cls') or ''}"
        href = safe_url(link.get("href"))
        live = bool(re.search(r"\b(live|online)\b", text, re.I)) or bool(
            re.search(r"live|online", str(link.get("cls") or ""), re.I)
        )
        if not live or not href:
            continue
        for name in wanted:
            if _token(name, text) or _token(name, href):
                slot = found.setdefault(name, {"online": False, "url": None})
                slot["online"] = True
                if not slot["url"]:
                    slot["url"] = href

    for name in wanted:
        slot = found.setdefault(name, {"online": False, "url": None})
        if slot["online"] and not slot["url"] and origin:
            slot["url"] = safe_url(f"{origin}/{name}")
        if not slot["online"]:
            slot["url"] = None
    return found
