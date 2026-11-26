"""Whatnot show status. PLAYING means the seller is live."""

from __future__ import annotations
import json
import urllib.request
from concurrent.futures import ThreadPoolExecutor

_QUERY = """query GetLivestreams($username: String, $size: Int, $statuses: [LiveStreamStatus!]) {
  getUser(username: $username) {
    livestreams(first: $size, statuses: $statuses) {
      edges { node {
        id
        status
        title
        activeViewers
        shop { edges { node {
          title
          transactionType
          status
          isLive
          currentBid { amount }
        } } }
      } }
    }
  }
}"""

_SALE = {"AUCTION", "BUY_IT_NOW", "BUY_NOW", "BIN"}
_GIFT = {"GIVEAWAY"}


def _title(node: dict) -> str:
    return " ".join(str(node.get("title") or "").split())


def _running(node: dict) -> bool:
    if node.get("isLive") is True:
        return True
    bid = node.get("currentBid") or {}
    if bid.get("amount") not in (None, 0, "0"):
        return True
    state = node.get("auctionState") or {}
    end = state.get("endTime")
    try:
        return float(end) > 0
    except (TypeError, ValueError):
        return False


def _pick(nodes, kinds) -> str:
    chosen = [n for n in nodes if str(n.get("transactionType") or "").upper() in kinds]
    live = [n for n in chosen if _running(n)]
    pool = live or []
    if not pool:
        return ""
    return _title(pool[0])


def parse_live(payload: dict) -> dict:
    edges = (
        ((payload or {}).get("data") or {}).get("getUser") or {}
    ).get("livestreams", {}).get("edges") or []
    for edge in edges:
        node = (edge or {}).get("node") or {}
        if str(node.get("status") or "").upper() != "PLAYING" or not node.get("id"):
            continue
        shop = ((node.get("shop") or {}).get("edges") or [])
        items = [(e or {}).get("node") or {} for e in shop]
        viewers = node.get("activeViewers")
        try:
            viewers = int(viewers)
        except (TypeError, ValueError):
            viewers = None
        return {
            "online": True,
            "url": f"https://www.whatnot.com/live/{node['id']}",
            "title": _title(node),
            "viewers": viewers,
            "item": _pick(items, _SALE),
            "giveaway": _pick(items, _GIFT),
        }
    return {"online": False, "url": None, "title": "", "viewers": None, "item": "", "giveaway": ""}


def button_text(name: str, info: dict) -> str:
    """Four lines: seller, show, viewers, the item on the block, the giveaway."""
    label = (name or "").upper() or "—"
    if not info or not info.get("online"):
        return label
    lines = [label]
    if info.get("title"):
        lines.append(str(info["title"])[:42])
    viewers = info.get("viewers")
    lines.append(f"{viewers:,} watching" if isinstance(viewers, int) else "live")
    lines.append("Now: " + (info.get("item") or "—"))
    lines.append("Gift: " + (info.get("giveaway") or "—"))
    return "\n".join(lines)


def _post(name: str) -> dict | None:
    last = None
    for _ in range(2):
        info = _ask(name)
        if info is None:
            return last
        last = info
        if info.get("online"):
            return info
    return last


def _ask(name: str) -> dict | None:
    payload = json.dumps({
        "operationName": "GetLivestreams",
        "query": _QUERY,
        "variables": {"size": 1, "statuses": ["PLAYING"], "username": name},
    }).encode()
    req = urllib.request.Request(
        "https://api.whatnot.com/graphql/?operationName=GetLivestreams",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0",
            "Origin": "https://www.whatnot.com",
            "apollographql-client-name": "web",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            return parse_live(json.loads(resp.read().decode()))
    except Exception:
        return None


def fetch_live(names) -> dict:
    wanted = [name for name in names if name]
    if not wanted:
        return {}
    found = {}
    with ThreadPoolExecutor(max_workers=min(9, len(wanted))) as pool:
        for name, info in zip(wanted, pool.map(_post, wanted)):
            if info is not None:
                found[name] = info
    return found
