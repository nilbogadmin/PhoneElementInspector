import json
import unittest

from core.show_page import read_show


def _frame(event, name):
    return json.dumps([None, "1", "public_auction:x", event, {"product": {"name": name}}])


class ShowPageTests(unittest.TestCase):
    def test_socket_sets_item_and_giveaway(self):
        info = read_show({}, [
            _frame("auction_started", "Charizard"),
            _frame("giveaway_started", "Free pack"),
        ])
        self.assertEqual(info["item"], "Charizard")
        self.assertEqual(info["giveaway"], "Free pack")

    def test_giveaway_won_clears_only_the_gift(self):
        info = read_show({}, [
            _frame("auction_started", "Charizard"),
            _frame("giveaway_started", "Free pack"),
            _frame("giveaway_won", "Free pack"),
        ])
        self.assertEqual(info["item"], "Charizard")
        self.assertEqual(info["giveaway"], "")

    def test_page_title_wins_over_the_socket(self):
        info = read_show(
            {"item": "On screen", "giveaway": "Live gift"},
            [_frame("auction_started", "Old"), _frame("giveaway_started", "Old gift")],
        )
        self.assertEqual(info, {"item": "On screen", "giveaway": "Live gift"})


if __name__ == "__main__":
    unittest.main()
