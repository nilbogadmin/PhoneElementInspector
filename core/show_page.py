"""Current item and giveaway from a Whatnot live tab."""

from __future__ import annotations
import json

_ITEM_EVENTS = {"auction_started", "new_bid", "product_updated", "auction_ended"}
_GIFT_ON = {"giveaway_started"}
_GIFT_OFF = {"giveaway_won", "giveaway_ended"}


def _clean(value) -> str:
    return " ".join(str(value or "").split())


def _product_name(payload: dict) -> str:
    product = payload.get("product") if isinstance(payload.get("product"), dict) else {}
    return _clean(product.get("name") or payload.get("name") or "")


def _events(frames):
    for text in frames or []:
        try:
            frame = json.loads(text)
        except Exception:
            continue
        if not isinstance(frame, list) or len(frame) < 5 or not isinstance(frame[4], dict):
            continue
        yield str(frame[3] or ""), frame[4]


def read_show(dom: dict | None, frames) -> dict:
    """The product on the player wins. The socket fills in until the page paints it."""
    item = ""
    gift = ""
    for event, payload in _events(frames):
        name = _product_name(payload)
        if event in _ITEM_EVENTS and name:
            item = name
        elif event in _GIFT_ON and name:
            gift = name
        elif event in _GIFT_OFF:
            gift = ""
    shown = _clean((dom or {}).get("item"))
    given = _clean((dom or {}).get("giveaway"))
    if shown:
        item = shown
    if given:
        gift = given
    return {"item": item, "giveaway": gift}
