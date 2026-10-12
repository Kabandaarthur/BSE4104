"""Minimal JSON-Schema subset validator for MCP tool arguments.

Implements exactly the keywords the tool schemas use (see src/mcp/schemas.py,
themselves the internal declarations' parameters): `type`, `properties`,
`required`, `enum`, `additionalProperties` and `items`. Anything richer is
deliberately out of scope: deep format rules that a plain JSON schema cannot
express cleanly (CASE-ID shape, summary length, student-number checks) are
enforced deterministically by the Week 4 handlers and surface as tool-
execution errors with `isError: true`, so this validator never drifts from
the handlers and the two layers cannot disagree.
"""


def _type_matches(instance, expected):
    if expected == "string":
        return isinstance(instance, str)
    if expected == "number":
        return isinstance(instance, (int, float)) and not isinstance(instance, bool)
    if expected == "integer":
        return isinstance(instance, int) and not isinstance(instance, bool)
    if expected == "boolean":
        return isinstance(instance, bool)
    if expected == "object":
        return isinstance(instance, dict)
    if expected == "array":
        return isinstance(instance, list)
    if expected == "null":
        return instance is None
    # Unknown type keyword: be lenient rather than guess (the schemas we
    # publish never use one).
    return True


def _walk(schema, instance, path, problems):
    expected = schema.get("type")
    if expected is not None and not _type_matches(instance, expected):
        problems.append(f"{path}: expected {expected}, got {type(instance).__name__}")
        return problems

    if "enum" in schema and instance not in schema["enum"]:
        problems.append(f"{path}: value {instance!r} is not one of {schema['enum']}")

    if isinstance(instance, dict):
        properties = schema.get("properties", {})
        for required in schema.get("required", []):
            if required not in instance:
                problems.append(f"{path}: missing required property {required!r}")
        for key, value in instance.items():
            if key not in properties:
                if schema.get("additionalProperties") is False:
                    problems.append(f"{path}: unexpected property {key!r}")
                continue
            _walk(properties[key], value, f"{path}.{key}", problems)

    if schema.get("items") is not None and isinstance(instance, list):
        for index, item in enumerate(instance):
            _walk(schema["items"], item, f"{path}[{index}]", problems)

    return problems


def validate_schema(schema, instance):
    """Return a list of human-readable problems against `schema`.

    An empty list means the instance is valid under the schema subset."""
    return _walk(schema, instance, "$", [])