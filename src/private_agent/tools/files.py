"""File search tool backed by Spotlight (mdfind) -- no special permissions needed."""

import os
import subprocess

_SEARCH_DIRS = ["Downloads", "Documents", "Desktop"]


def search_files(query: str, limit: int = 10) -> str:
    """Search this Mac's files by filename using Spotlight.

    Args:
        query: A filename fragment to search for, e.g. "resume" or "invoice".
        limit: Maximum number of results to return.
    """
    home = os.path.expanduser("~")
    dirs = [os.path.join(home, d) for d in _SEARCH_DIRS if os.path.isdir(os.path.join(home, d))]
    if not dirs:
        return "No searchable directories found."

    onlyin_args = []
    for d in dirs:
        onlyin_args.extend(["-onlyin", d])

    try:
        result = subprocess.run(
            ["mdfind", "-name", query, *onlyin_args],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except subprocess.TimeoutExpired:
        return "Search timed out."

    paths = [p for p in result.stdout.splitlines() if p.strip()][:limit]
    if not paths:
        return f"No files found matching '{query}' in {', '.join(_SEARCH_DIRS)}."
    return "\n".join(paths)
