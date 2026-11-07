"""eventline: the alphabet and the length of the values (WATCH-04)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import eventline  # noqa: E402


def test_equals_space_newline_and_control_characters_become_underscores():  # WATCH-04
    assert eventline.clean("a=b c\nd\r\te\x00f") == "a_b_c_d__e_f"


def test_long_values_are_cut_and_empty_ones_become_a_dash():  # WATCH-04
    assert eventline.clean("x" * 500) == "x" * eventline.MAX_VALUE
    assert eventline.clean("") == "-" and eventline.clean("é") == "_"


def test_safe_characters_pass_and_none_values_are_skipped():  # WATCH-04
    assert eventline.clean("12036@g.us:+5,a-b_c") == "12036@g.us:+5,a-b_c"
    assert eventline.line([("a", 1), ("b", None), ("c", False)]) == "a=1 c=False"


def test_every_value_of_a_line_is_clean_whatever_the_input():  # WATCH-04
    out = eventline.line([("chat", "x y\nstate=owned"), ("sender", "a=b")])
    assert "\n" not in out and out.count(" ") == 1 and out.count("=") == 2
