import unittest

from core.whatnot_live import button_text, parse_live, parse_stream


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

    def test_button_always_has_five_lines(self):
        live = button_text("krakenhits", parse_live(_payload())).splitlines()
        self.assertEqual(live, [
            "KRAKENHITS",
            "CRAZY ETB WALL",
            "1,013 watching",
            "Now: —",
            "Gift: —",
        ])
        offline = button_text("krakenhits", parse_live(
            {"data": {"getUser": {"livestreams": {"edges": []}}}}
        )).splitlines()
        self.assertEqual(offline[0], "KRAKENHITS")
        self.assertEqual(offline[1], "offline")
        self.assertEqual(len(offline), 5)
        self.assertEqual(len(button_text("krakenhits", {}).splitlines()), 5)


if __name__ == "__main__":
    unittest.main()
