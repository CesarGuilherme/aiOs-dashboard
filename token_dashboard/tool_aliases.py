"""Cross-agent tool name sets for tips / Brain ROI / knowledge suggestions.

DB stores raw tool names (Claude: Read; Grok: read_file). Queries expand these sets.
"""

from __future__ import annotations

# File-read tools that indicate re-derivation candidates for memory suggestions.
READ_TOOLS = ("Read", "read_file", "Grep", "grep", "open_page_with_find")

# Broader file-touch set used by waste-pattern tips.
FILE_TOOLS = (
    "Read", "read_file",
    "Edit", "search_replace", "write", "Write",
    "Glob", "list_dir",
    "Grep", "grep",
)

# Shell / terminal
BASH_TOOLS = ("Bash", "run_terminal_command", "Shell")

# Async-wait / poll tools — sleep loops and TaskOutput polling.
POLL_TOOLS = ("TaskOutput", "get_command_or_subagent_output", "AwaitShell")


def sql_in(names: tuple) -> str:
    """Return `IN ('a','b')` fragment — names are code constants, not user input."""
    return "IN (" + ",".join(repr(n) for n in names) + ")"
