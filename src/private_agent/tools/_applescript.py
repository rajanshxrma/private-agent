"""Shared helper for safely interpolating strings into AppleScript source.

Any tool argument that ends up inside a `"..."` AppleScript string literal --
title, subject, body, recipient, etc -- can contain characters an LLM
generated, including literal double quotes and backslashes. Without escaping,
a value like `Based on the file name "resumePF.pdf"...` breaks the AppleScript
parser (confirmed in orchard, a sibling project reusing these same tools: this
exact case failed with a syntax error on unescaped input during real testing,
not a hypothetical).
"""


def escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')
