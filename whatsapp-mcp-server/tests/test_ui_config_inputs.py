"""Typed input parsing (CFG-02): raw text from a control becomes the value it stands for, or an English reason."""
from ui.config.fields import Field
from ui.config.inputs import from_raw, to_raw


def f(kind, **kw):
    return Field("sample", "Sample", kind, **kw)


def test_integers_are_read_with_surrounding_spaces_and_signs():
    assert from_raw(f("int"), " 42 ").value == 42
    assert from_raw(f("int"), "-7").value == -7
    assert from_raw(f("int"), "+3").value == 3
    assert from_raw(f("int"), "0").value == 0


def test_decimals_accept_a_comma_or_a_dot():
    assert from_raw(f("float"), "1,5").value == 1.5
    assert from_raw(f("float"), "1.5").value == 1.5
    assert from_raw(f("float"), "  .25 ").value == 0.25
    assert from_raw(f("float"), "3").value == 3.0
    assert from_raw(f("float"), "-0,5").value == -0.5


def test_a_malformed_number_gives_the_reason_and_keeps_what_was_typed():
    for kind, bad in (("int", "12abc"), ("int", "1.5"), ("int", "1,5"), ("int", "--1"), ("float", "1,2,3"),
                      ("float", "abc"), ("float", "nan"), ("float", "inf"), ("float", "1e3"), ("float", "1..2")):
        result = from_raw(f(kind), bad)
        assert result.ok is False, (kind, bad)
        assert result.error and "number" in result.error
        assert result.raw == bad
        assert bad not in result.error


def test_empty_text_is_no_value_for_scalars():
    for kind in ("int", "float", "text"):
        for blank in ("", "   ", "\n"):
            result = from_raw(f(kind), blank)
            assert result.ok and result.value is None


def test_text_is_trimmed_at_the_ends_only():
    assert from_raw(f("text"), "  hello  world \n").value == "hello  world"


def test_lists_are_read_one_item_per_line_dropping_blank_lines():
    result = from_raw(f("list"), " alpha \n\n  beta\r\n gamma  \n   \n")
    assert result.value == ["alpha", "beta", "gamma"]
    assert from_raw(f("list"), "").value == []
    assert from_raw(f("list"), "  \n \n").value == []


def test_a_secret_keeps_its_text_untrimmed_and_empty_means_nothing_typed():
    assert from_raw(f("secret"), "  pass word  ").value == "  pass word  "
    result = from_raw(Field("api_token", "Token", "secret"), "")
    assert result.ok and result.value is None


def test_values_that_are_not_text_pass_through_to_the_field_check():
    assert from_raw(f("bool"), True).value is True
    assert from_raw(f("choice", options=("a",)), "a").value == "a"
    assert from_raw(f("multichoice", options=("a", "b")), ["a"]).value == ["a"]
    records = [{"name": "x"}]
    assert from_raw(f("records", fields=(Field("name", "Name", "text"),)), records).value == records
    assert from_raw(f("int"), 5).value == 5            # already typed
    assert from_raw(f("text"), None).value is None


def test_to_raw_is_the_inverse_for_what_a_control_shows():
    assert to_raw(f("int"), 42) == "42"
    assert to_raw(f("float"), 1.5) == "1.5"
    assert to_raw(f("float"), 2.0) == "2"
    assert to_raw(f("list"), ["a", "b"]) == "a\nb"
    assert to_raw(f("text"), "hi") == "hi"
    assert to_raw(f("text"), None) == ""
    assert to_raw(f("int"), None) == ""
    assert from_raw(f("list"), to_raw(f("list"), ["x", "y z"])).value == ["x", "y z"]
    assert from_raw(f("float"), to_raw(f("float"), 0.125)).value == 0.125
