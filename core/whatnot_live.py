"""Whatnot show status that stays current.

Finding a show through the seller list is flaky: Whatnot often returns an
empty list for someone who is live. Once a show id is known, refreshing
that id is stable, so the board keeps the id and polls it until the show ends.
"""

from __future__ import annotations
import json
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

_PLAYING = """query GetLivestreams($username: String!, $size: Int, $statuses: [LiveStreamStatus!]) {
  getUser(username: $username) {
    livestreams(first: $size, statuses: $statuses) {
      edges { node { id status title activeViewers } }
    }
  }
}"""

_ANY = """query GetLivestreams($username: String!, $size: Int) {
  getUser(username: $username) {
    livestreams(first: $size) {
      edges { node { id status title activeViewers } }
    }
  }
}"""

_BY_ID = """query LiveStream($id: ID!) {
  liveStream(id: $id) { id status title activeViewers }
}"""

_SHOP = """query LiveShopFeed($liveId: ID!, $first: Int) {
  liveShop(liveId: $liveId) {
    feed {
      objects(first: $first) {
        edges { node {
          ... on Section {
            sectionType
            contents { edges { node { ... on ListingNode { title transactionType } } } }
          }
        } }
      }
    }
  }
}"""

# The saved button name has a typo. The live account is traderbeaplus.
_ALIAS = {"traderbeapllus": "traderbeaplus"}


def _title(node: dict) -> str:
    return " ".join(str(node.get("title") or "").split())


def _viewers(node: dict):
    try:
        return int(node.get("activeViewers"))
    except (TypeError, ValueError):
        return None


def _from_node(node: dict) -> dict:
    return {
        "online": True,
        "url": f"https://www.whatnot.com/live/{node['id']}",
        "title": _title(node),
        "viewers": _viewers(node),
        "item": "",
        "giveaway": "",
        "known": True,
    }


def _offline(known: bool) -> dict:
    return {
        "online": False, "url": None, "title": "", "viewers": None,
        "item": "", "giveaway": "", "known": known,
    }


def parse_live(payload: dict) -> dict:
    user = ((payload or {}).get("data") or {}).get("getUser")
    edges = ((user or {}).get("livestreams") or {}).get("edges") or []
    for edge in edges:
        node = (edge or {}).get("node") or {}
        if str(node.get("status") or "").upper() == "PLAYING" and node.get("id"):
            return _from_node(node)
    if user is None:
        return _offline(False)
    return _offline(True)


def parse_stream(payload: dict) -> dict | None:
    """None means the request failed. A null show is a real offline answer."""
    if not isinstance(payload, dict) or payload.get("data") is None and "errors" in (payload or {}):
        return None
    data = (payload or {}).get("data")
    if not isinstance(data, dict) or "liveStream" not in data:
        return None
    node = data.get("liveStream") or {}
    if str(node.get("status") or "").upper() == "PLAYING" and node.get("id"):
        return _from_node(node)
    return _offline(True)


def button_text(name: str, info: dict) -> str:
    """Seller, show, viewers, and the giveaway. No item-for-sale line."""
    label = (name or "").upper() or "—"
    if not info:
        show, viewers = "checking", "—"
    elif not info.get("online"):
        show, viewers = "offline", "—"
    else:
        show = (info.get("title") or "live")[:42]
        viewers = f"{info['viewers']:,} watching" if isinstance(info.get("viewers"), int) else "LIVE"
    gift = _title({"title": (info or {}).get("giveaway")}) if info else ""
    if gift.lower() in {"sold", "buy now", "entered", "enter giveaway"}:
        gift = ""
    return "\n".join([label, show, viewers, gift or "—"])


def _post_query(query: str, variables: dict, operation: str = "GetLivestreams") -> dict | None:
    payload = json.dumps({
        "operationName": operation,
        "query": query,
        "variables": variables,
    }).encode()
    req = urllib.request.Request(
        "https://api.whatnot.com/graphql/?operationName=" + operation,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0",
            "Origin": "https://www.whatnot.com",
            "Referer": "https://www.whatnot.com/",
            "apollographql-client-name": "web",
            "x-whatnot-app": "whatnot-web",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            return json.loads(resp.read().decode())
    except Exception:
        return None


def shop_giveaway(payload: dict) -> str:
    """The first giveaway in the live shop is the one on screen."""
    edges = (
        (((payload or {}).get("data") or {}).get("liveShop") or {})
        .get("feed", {})
        .get("objects", {})
        .get("edges")
        or []
    )
    for edge in edges:
        node = (edge or {}).get("node") or {}
        if str(node.get("sectionType") or "") != "SHOP_GIVEAWAYS":
            continue
        for item in ((node.get("contents") or {}).get("edges") or []):
            listing = (item or {}).get("node") or {}
            if str(listing.get("transactionType") or "").upper() != "GIVEAWAY":
                continue
            title = _title(listing)
            if title:
                return title
    return ""


def _shop_giveaway(show_id: str) -> str:
    payload = _post_query(_SHOP, {"liveId": show_id, "first": 40}, operation="LiveShopFeed")
    if payload is None:
        return ""
    return shop_giveaway(payload)


def _by_id(show_id: str) -> dict | None:
    payload = _post_query(_BY_ID, {"id": show_id}, operation="LiveStream")
    if payload is None:
        return None
    return parse_stream(payload)


def _read(name: str, tries: int = 4) -> dict | None:
    """Return the live show, or None when Whatnot did not give a real answer."""
    names = [name]
    alias = _ALIAS.get((name or "").lower())
    if alias and alias not in names:
        names.append(alias)
    for candidate in names:
        for _ in range(max(1, tries)):
            for query, variables in (
                (_PLAYING, {"username": candidate, "size": 1, "statuses": ["PLAYING"]}),
                (_ANY, {"username": candidate, "size": 3}),
            ):
                payload = _post_query(query, variables)
                if payload is None:
                    continue
                info = parse_live(payload)
                if info.get("online"):
                    return info
    return None


def fetch_live(names) -> dict:
    wanted = []
    for name in names:
        if name and name not in wanted:
            wanted.append(name)
    if not wanted:
        return {}
    found = {}
    with ThreadPoolExecutor(max_workers=min(9, len(wanted))) as pool:
        for name, info in zip(wanted, pool.map(_read, wanted)):
            if info is not None:
                found[name] = info
    return found


class ShowBoard:
    """One refresher. Known shows are polled by id so the numbers stay current."""

    def __init__(self):
        self._lock = threading.Lock()
        self._names: list[str] = []
        self._info: dict[str, dict] = {}
        self._ids: dict[str, str] = {}
        self._misses: dict[str, int] = {}
        self._alive = True
        self.thread = threading.Thread(target=self._run, name="whatnot-board", daemon=True)
        self.thread.start()

    def set_names(self, names) -> None:
        clean = []
        for name in names:
            if name and name not in clean:
                clean.append(name)
        with self._lock:
            self._names = clean
            for name in list(self._ids):
                if name not in clean:
                    self._ids.pop(name, None)
                    self._info.pop(name, None)

    def snapshot(self) -> dict:
        with self._lock:
            return {key: dict(value) for key, value in self._info.items()}

    def close(self) -> None:
        self._alive = False

    def _store(self, name: str, info: dict, show_id: str | None) -> None:
        with self._lock:
            if info.get("online") and show_id:
                self._ids[name] = show_id
            elif info.get("known") and not info.get("online"):
                self._ids.pop(name, None)
            self._info[name] = info

    def _refresh(self, name: str) -> None:
        with self._lock:
            show_id = self._ids.get(name)
            had_offline = bool(self._info.get(name)) and not self._info[name].get("online")
        if show_id:
            info = _by_id(show_id)
            if info is None:
                return
            if info.get("online"):
                gift = _shop_giveaway(show_id)
                with self._lock:
                    previous = (self._info.get(name) or {}).get("giveaway") or ""
                info["giveaway"] = gift or previous
            self._store(name, info, show_id if info.get("online") else None)
            return
        info = _read(name, tries=1 if had_offline else 4)
        if info and info.get("online"):
            show_id = info["url"].rsplit("/", 1)[-1]
            info["giveaway"] = _shop_giveaway(show_id)
            with self._lock:
                self._misses[name] = 0
            self._store(name, info, show_id)
            return
        with self._lock:
            misses = self._misses.get(name, 0) + 1
            self._misses[name] = misses
            already = name in self._info
        if not already and misses >= 3:
            self._store(name, _offline(True), None)

    def _run(self) -> None:
        while self._alive:
            with self._lock:
                names = list(self._names)
            if names:
                with ThreadPoolExecutor(max_workers=min(9, len(names))) as pool:
                    list(pool.map(self._refresh, names))
            for _ in range(8):
                if not self._alive:
                    return
                time.sleep(0.5)
