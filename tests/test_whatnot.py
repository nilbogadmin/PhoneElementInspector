import unittest

from core.whatnot_live import button_text, parse_live


def _payload():
    return {"data": {"getUser": {"livestreams": {"edges": [{"node": {
        "id": "af5b3c8f-0f77-415f-b96f-a044e8857f40",
        "status": "PLAYING",
        "title": "CRAZY ETB WALL",
        "activeViewers": 1013,
        "shop": {"edges": [
            {"node": {"title": "Next pack", "transactionType": "AUCTION", "status": "ACTIVE", "isLive": False}},
            {"node": {"title": "Charizard", "transactionType": "AUCTION", "status": "ACTIVE", "isLive": True, "currentBid": {"amount": 12}}},
            {"node": {"title": "Old gift", "transactionType": "GIVEAWAY", "status": "SOLD", "isLive": False}},
            {"node": {"title": "Free pack", "transactionType": "GIVEAWAY", "status": "ACTIVE", "isLive": True}},
        ]},
    }}]}}}}


class WhatnotTests(unittest.TestCase):
    def test_live_show_keeps_only_the_running_item_and_giveaway(self):
        info = parse_live(_payload())
        self.assertTrue(info["online"])
        self.assertEqual(info["title"], "CRAZY ETB WALL")
        self.assertEqual(info["viewers"], 1013)
        self.assertEqual(info["item"], "Charizard")
        self.assertEqual(info["giveaway"], "Free pack")
        self.assertIn("/live/af5b3c8f-0f77-415f-b96f-a044e8857f40", info["url"])

    def test_offline_when_nothing_is_playing(self):
        info = parse_live({"data": {"getUser": {"livestreams": {"edges": []}}}})
        self.assertFalse(info["online"])
        self.assertEqual(button_text("krakenhits", info), "KRAKENHITS")

    def test_button_lines(self):
        text = button_text("krakenhits", parse_live(_payload()))
        self.assertEqual(text.splitlines(), [
            "KRAKENHITS",
            "CRAZY ETB WALL",
            "1,013 watching",
            "Now: Charizard",
            "Gift: Free pack",
        ])


if __name__ == "__main__":
    unittest.main()
