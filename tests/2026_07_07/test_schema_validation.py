"""validate_json_schema 最小子集（ADR-0011 §3.7，2026-07-07 切片）。"""

from forgecli.application.llm.gateway import validate_json_schema

_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "risk": {"type": "string", "enum": ["low", "medium", "high"]},
        "steps": {"type": "array", "items": {"type": "string"}},
        "count": {"type": "integer"},
    },
    "required": ["title", "risk"],
}


def test_valid_data_passes() -> None:
    data = {"title": "t", "risk": "low", "steps": ["a"], "count": 3}
    assert validate_json_schema(data, _SCHEMA) == []


def test_missing_required_field_reported() -> None:
    errors = validate_json_schema({"title": "t"}, _SCHEMA)
    assert any("risk" in error for error in errors)


def test_wrong_type_reported() -> None:
    errors = validate_json_schema({"title": 1, "risk": "low"}, _SCHEMA)
    assert any("title" in error for error in errors)


def test_enum_violation_reported() -> None:
    errors = validate_json_schema({"title": "t", "risk": "extreme"}, _SCHEMA)
    assert any("enum" in error for error in errors)


def test_array_items_validated() -> None:
    data = {"title": "t", "risk": "low", "steps": ["ok", 42]}
    errors = validate_json_schema(data, _SCHEMA)
    assert any("steps[1]" in error for error in errors)


def test_bool_is_not_integer() -> None:
    errors = validate_json_schema({"title": "t", "risk": "low", "count": True}, _SCHEMA)
    assert any("count" in error for error in errors)


def test_additional_properties_false_rejects_extras() -> None:
    schema = {
        "type": "object",
        "properties": {"a": {"type": "string"}},
        "additionalProperties": False,
    }
    errors = validate_json_schema({"a": "x", "b": 1}, schema)
    assert any("b" in error for error in errors)


def test_top_level_type_mismatch() -> None:
    errors = validate_json_schema(["not", "an", "object"], _SCHEMA)
    assert errors
