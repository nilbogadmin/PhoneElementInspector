"""Whatnot show status. PLAYING means the seller is live."""

from __future__ import annotations
import json
import urllib.request

_QUERY = """query GetLivestreams($username: String, $size: Int, $statuses: [LiveStreamStatus!]) {
  getUser(username: $username) {
    username
    livestreams(first: $size, statuses: $statuses) {
      edges { node { id status } }
    }
  }
}"""


def parse_live(payload: dict) -> dict:
    edges = (
        ((payload or {}).get("data") or {}).get("getUser") or {}
    ).get("livestreams", {}).get("edges") or []
    for edge in edges:
        node = (edge or {}).get("node") or {}
        if str(node.get("status") or "").upper() == "PLAYING" and node.get("id"):
            return {"online": True, "url": f"https://www.whatnot.com/live/{node['id']}"}
    return {"online": False, "url": None}


def fetch_live(names) -> dict:
    found = {}
    for name in names:
        if not name:
            continue
        payload = json.dumps({
            "operationName": "GetLivestreams",
            "query": _QUERY,
            "variables": {"size": 5, "statuses": ["PLAYING"], "username": name},
        }).encode()
        req = urllib.request.Request(
            "https://api.whatnot.com/graphql/?operationName=GetLivestreams",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "Mozilla/5.0",
                "apollographql-client-name": "web",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                found[name] = parse_live(json.loads(resp.read().decode()))
        except Exception:
            continue
    return found
