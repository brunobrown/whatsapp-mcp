"""Field model and checks (CFG-02, CFG-10): each kind accepts valid and refuses invalid values, in English."""
import pytest

from ui.config.fields import Field, Problem


def make(kind, **kw):
    return Field("sample", "Sample", kind, **kw)


@pytest.mark.parametrize("kind", ["bool", "int", "float", "text", "choice", "multichoice", "secret", "list", "records"])
def test_every_kind_is_declarable(kind):
    extra = {"options": ("a", "b")} if "choice" in kind else {}
    if kind == "records":
        extra = {"fields": (Field("name", "Name", "text"),)}
    assert make(kind, **extra).kind == kind


def test_an_unknown_kind_is_refused_at_declaration():
    with pytest.raises(ValueError):
        make("color")


def test_bool_accepts_booleans_only():
    f = make("bool")
    assert f.check(True) is None and f.check(False) is None
    assert f.check("true") is not None
    assert f.check(1) is not None


def test_int_accepts_whole_numbers_inside_bounds():
    f = make("int", bounds=(1, 10))
    assert f.check(1) is None and f.check(10) is None
    assert "at least 1" in f.check(0)
    assert "at most 10" in f.check(11)
    assert f.check(2.5) is not None
    assert f.check(True) is not None          # a boolean is not a number
    assert f.check("3") is not None


def test_float_accepts_numbers_inside_bounds_and_refuses_nan_and_infinity():
    f = make("float", bounds=(0, 60))
    assert f.check(0) is None and f.check(2.5) is None and f.check(60.0) is None
    assert "at most 60" in f.check(60.5)
    assert "at least 0" in f.check(-0.1)
    assert f.check(float("nan")) is not None
    assert f.check(float("inf")) is not None
    assert f.check(True) is not None


def test_text_accepts_a_string_within_the_length_limit():
    f = make("text", max_length=5)
    assert f.check("hello") is None
    assert "5 characters" in f.check("hello!")
    assert f.check(5) is not None


def test_a_giant_value_is_refused_with_the_limit_stated_and_not_echoed():
    f = make("text")
    reason = f.check("x" * 10_000_000)
    assert str(f.max_length) in reason
    assert "xxxx" not in reason


def test_control_characters_are_refused_in_a_single_line_text():
    f = make("text")
    for bad in ("a\x00b", "a\nb", "a\tb", "a\x1bb", "a\x7fb"):
        assert "control characters" in f.check(bad)


def test_multiline_text_allows_line_breaks_and_tabs_but_not_other_controls():
    f = make("text", multiline=True)
    assert f.check("line one\nline two\ttabbed\r\n") is None
    assert "control characters" in f.check("a\x00b")


def test_choice_accepts_only_a_listed_option():
    f = make("choice", options=("draft", "auto"))
    assert f.check("draft") is None
    reason = f.check("secretvalue")
    assert "draft" in reason and "auto" in reason
    assert "secretvalue" not in reason


def test_multichoice_accepts_a_list_of_listed_options_without_duplicates():
    f = make("multichoice", options=("a", "b", "c"))
    assert f.check([]) is None and f.check(["a", "c"]) is None
    assert f.check(["a", "z"]) is not None
    assert f.check(["a", "a"]) is not None
    assert f.check("a") is not None


def test_list_checks_every_item_and_the_number_of_items():
    f = make("list", max_length=5, max_items=2)
    assert f.check(["ab", "cd"]) is None
    assert "5 characters" in f.check(["abcdef"])
    assert "2 items" in f.check(["a", "b", "c"])
    assert "control characters" in f.check(["a\nb"])
    assert f.check([1]) is not None
    assert f.check("a") is not None


def test_secret_accepts_non_empty_text_within_the_limit():
    f = make("secret", max_length=8)
    assert f.check("12345678") is None
    assert f.check("") is not None
    assert "8 characters" in f.check("123456789")
    assert f.check(12) is not None
    assert "control characters" in f.check("a\x00b")


def test_records_check_each_sub_field_and_name_the_record():
    inner = (Field("name", "Name", "text", max_length=10), Field("count", "Count", "int", bounds=(1, 5)))
    f = make("records", fields=inner, id_key="name")
    assert f.check([{"name": "one", "count": 2}, {"name": "two", "count": 5}]) is None
    reason = f.check([{"name": "one", "count": 9}])
    assert "record 1" in reason and "Count" in reason and "at most 5" in reason
    assert "record 2" in f.check([{"name": "one"}, {"name": "x" * 11}])
    assert f.check([{"name": "one"}, {"name": "one"}]) is not None       # the id repeats
    assert f.check([{"name": "one", "other": 1}]) is not None            # an undeclared sub-field
    assert f.check(["not a dict"]) is not None
    assert f.check({"name": "one"}) is not None


def test_none_means_no_value_and_is_accepted_for_every_kind():
    for kind, extra in (("bool", {}), ("int", {}), ("text", {}), ("choice", {"options": ("a",)}), ("secret", {})):
        assert make(kind, **extra).check(None) is None


def test_a_field_named_like_a_secret_must_be_declared_secret():
    for key in ("override_token", "approval_token", "webhook_secret", "password", "api_key", "db_password"):
        with pytest.raises(ValueError):
            Field(key, "Label", "text")
        assert Field(key, "Label", "secret").secret is True
    assert Field("excerpt_chars", "Chars", "int").secret is False
    assert Field("session_taint", "Taint", "text").secret is False


def test_a_secret_field_cannot_carry_a_default_value():
    with pytest.raises(ValueError):
        Field("override_token", "Token", "secret", default="x")


def test_bounds_must_be_ordered_and_options_required_for_choices():
    with pytest.raises(ValueError):
        make("int", bounds=(5, 1))
    with pytest.raises(ValueError):
        make("choice")
    with pytest.raises(ValueError):
        make("multichoice")


def test_records_need_sub_fields_with_unique_keys():
    with pytest.raises(ValueError):
        make("records")
    dup = (Field("a", "A", "text"), Field("a", "A2", "text"))
    with pytest.raises(ValueError):
        make("records", fields=dup)
    with pytest.raises(ValueError):
        make("records", fields=(Field("a", "A", "text"),), id_key="missing")


def test_problem_carries_the_field_and_the_reason():
    p = Problem("mode", "must be one of: a")
    assert p.key == "mode" and p.message == "must be one of: a"
    assert Problem(None, "general").key is None


def test_a_field_group_is_validated_and_defaults_to_empty():
    """UXP-15: the group label is optional, empty by default and at most 40 characters."""
    assert make("text").group == ""
    assert make("text", group="Timing").group == "Timing"
    assert make("text", group="x" * 40).group == "x" * 40
    with pytest.raises(ValueError):
        make("text", group="x" * 41)
