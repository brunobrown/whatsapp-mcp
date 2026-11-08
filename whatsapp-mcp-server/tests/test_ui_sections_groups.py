"""Field groups (UXP-15): every section with many fields is split into at least two titled groups."""
from ui.api.forms import field_schema
from ui.config.registry import Registry


def sections():
    registry = Registry()
    registry.discover("ui.sections")
    return registry.sections()


def test_each_section_with_more_than_four_fields_has_at_least_two_groups():
    """UXP-15: more than four top-level fields means at least two distinct group labels, none left without one."""
    crowded = [s for s in sections() if len(s.fields) > 4]
    assert {s.id for s in crowded} >= {"send_policy", "channel", "database"}
    for section in crowded:
        groups = {f.group for f in section.fields}
        assert "" not in groups, section.id
        assert len(groups) >= 2, section.id


def test_group_labels_are_short_english_text():
    """UXP-15: a label is non-empty text of at most 40 characters, and it reaches the schema unchanged."""
    for section in sections():
        for f in section.fields:
            assert field_schema(f)["group"] == f.group
            if f.group:
                assert f.group.strip() == f.group and len(f.group) <= 40
                assert f.group.isascii() and f.group[0].isupper(), (section.id, f.group)
