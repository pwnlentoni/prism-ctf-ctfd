def to_bool(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        if (v := {"true": True, "false": False}.get(value)) is not None:
            return v
    raise Exception(f"Bad boolean value {value!r}")
