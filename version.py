"""Version of studio_render, written in the projects (.srp) it saves."""
VERSION = "1.3.2.0"


def parse(text):
    """(major, minor, patch, build) of a version string, missing parts are 0."""
    parts = []
    for part in str(text).split(".")[:4]:
        digits = "".join(c for c in part if c.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts + [0] * (4 - len(parts)))
