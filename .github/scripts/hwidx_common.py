"""Helpers shared by the hwidx PR check scripts."""

import os
import re
import subprocess


def git(*args):
    return subprocess.run(
        ["git", "-c", "core.quotePath=false", *args],
        check=True, capture_output=True, text=True,
    ).stdout


def md(text):
    """Escape untrusted text (names, paths, URLs) for a GitHub comment."""
    text = re.sub(r"[\x00-\x1f\x7f]", " ", str(text))
    entities = {
        "&": "&amp;", "<": "&lt;", ">": "&gt;", "|": "&#124;", "@": "&#64;",
        "[": "&#91;", "]": "&#93;", "*": "&#42;", "_": "&#95;", "`": "&#96;",
        "~": "&#126;", "\\": "&#92;", "#": "&#35;", "!": "&#33;",
    }
    return "".join(entities.get(c, c) for c in text)


def write_output(name, value):
    """Append a single-line output for the current GitHub Actions step."""
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(f"{name}={value}\n")


def code(text):
    """Render untrusted text as an inline code span, safe inside tables."""
    text = re.sub(r"[\x00-\x1f\x7f]", " ", str(text))
    return "`" + text.replace("`", "'").replace("|", "\\|") + "`"
