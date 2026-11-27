import unittest

from core.whatnot_live import button_text, parse_live, parse_stream, shop_giveaway


def _payload():
    return {"data": {"getUser": {"livestreams": {"edges": [{"node": {
        "id": "af5b3c8f-0f77-415f-b96f-a044e8857f40",
        "status": "PLAYING",
        "title": "CRAZY ETB WALL",
        "activeViewers": 1013,
    }}]}}}}


class WhatnotTests(unittest.TestCase):
    def test_live_show_reports_title_and_viewers(self):
        info = parse_live(_payload())
        self.assertTrue(info["online"])
        self.assertEqual(info["title"], "CRAZY ETB WALL")
        self.assertEqual(info["viewers"], 1013)
        self.assertIn("/live/af5b3c8f-0f77-415f-b96f-a044e8857f40", info["url"])

    def test_show_by_id_stays_current(self):
        info = parse_stream({"data": {"liveStream": {
            "id": "abc", "status": "PLAYING", "title": "ETB", "activeViewers": 50,
        }}})
        self.assertEqual(info["viewers"], 50)
        self.assertTrue(info["online"])
        ended = parse_stream({"data": {"liveStream": None}})
        self.assertFalse(ended["online"])
        self.assertTrue(ended["known"])

    def test_empty_list_for_a_real_user_is_offline(self):
        info = parse_live({"data": {"getUser": {"livestreams": {"edges": []}}}})
        self.assertTrue(info["known"])
        self.assertFalse(info["online"])

    def test_null_user_is_not_an_offline_answer(self):
        info = parse_live({"data": {"getUser": None}})
        self.assertFalse(info["known"])

    def test_first_giveaway_in_the_shop_is_the_one_on_screen(self):
        payload = {"data": {"liveShop": {"feed": {"objects": {"edges": [
            {"node": {"sectionType": "SHOP_PRODUCTS", "contents": {"edges": [
                {"node": {"title": "Pack", "transactionType": "AUCTION"}},
            ]}}},
            {"node": {"sectionType": "SHOP_GIVEAWAYS", "contents": {"edges": [
                {"node": {"title": "$20 Whatnot Gift Card", "transactionType": "GIVEAWAY"}},
                {"node": {"title": "$25 Whatnot Gift Card", "transactionType": "GIVEAWAY"}},
            ]}}},
        ]}}}}}
        self.assertEqual(shop_giveaway(payload), "$20 Whatnot Gift Card")
        shown = dict(parse_live(_payload()))
        shown["giveaway"] = "$20 Whatnot Gift Card"
        self.assertEqual(button_text("krakenhits", shown).splitlines(), [
            "KRAKENHITS",
            "CRAZY ETB WALL",
            "1,013 watching",
            "$20 Whatnot Gift Card",
        ])
        self.assertNotIn("Now:", button_text("krakenhits", {"online": True, "title": "Show", "viewers": 1, "item": "Pack", "giveaway": "Sold"}))
        self.assertEqual(button_text("krakenhits", {"online": True, "title": "Show", "viewers": 1, "giveaway": "Sold"}).splitlines()[-1], "—")
        offline = button_text("krakenhits", parse_live(
            {"data": {"getUser": {"livestreams": {"edges": []}}}}
        )).splitlines()
        self.assertEqual(offline[0], "KRAKENHITS")
        self.assertEqual(offline[1], "offline")
        self.assertEqual(len(offline), 4)
        self.assertEqual(len(button_text("krakenhits", {}).splitlines()), 4)


if __name__ == "__main__":
    unittest.main()
