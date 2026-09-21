"""Tests for regex channel sort modes."""

import unittest

from src.config import _build_multiview_block
from src.regex_order import _top_level_alternatives, regex_sort_key


class RegexOrderTests(unittest.TestCase):
    def test_pattern_order_follows_top_level_alternatives(self):
        pattern = "event-a|event-b|event-c|event-d|event-e"
        names = ["event-e", "event-b", "event-d", "event-a", "event-c"]

        names.sort(key=lambda name: regex_sort_key(pattern, name, 1, "pattern"))

        self.assertEqual(names, ["event-a", "event-b", "event-c", "event-d", "event-e"])

    def test_pattern_order_keeps_nested_alternatives_together(self):
        self.assertEqual(_top_level_alternatives("event-(a|b)|event-c"), ["event-(a|b)", "event-c"])

    def test_channel_number_remains_the_default_order(self):
        self.assertLess(
            regex_sort_key("first|second", "second", 2, "channel_number"),
            regex_sort_key("first|second", "first", 10, "channel_number"),
        )

    def test_channel_number_reverse_sorts_highest_first(self):
        self.assertLess(
            regex_sort_key("first|second", "first", 10, "channel_number_reverse"),
            regex_sort_key("first|second", "second", 2, "channel_number_reverse"),
        )

    def test_regex_pattern_follows_sort_as_a_textarea(self):
        fields = _build_multiview_block("test0001", 1, 4, selector_type="regex")
        regex_fields = [field for field in fields if "regex_" in field["id"]]

        self.assertEqual(
            [field["id"] for field in regex_fields],
            ["multiview_test0001_regex_sort", "multiview_test0001_regex_pattern"],
        )
        self.assertEqual(regex_fields[1]["type"], "textarea")
