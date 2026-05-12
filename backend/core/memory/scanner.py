"""Lightweight injection / exfiltration scanner for curated memory content.

Notes written via the ``memory`` tool end up **inside the system prompt** on
the next conversation (hermes-style frozen snapshot pattern). If a malicious
or careless user told the agent to remember a payload like ``"ignore all
prior instructions"``, that string would later be rendered as part of the
trusted system block — effectively a stored prompt-injection.

This scanner is a coarse first line of defense: a regex / unicode filter
that rejects the most obvious injection and exfiltration patterns at write
time. It is **not** a substitute for adversarial review of saved memories;
think of it as the seat belt, not the airbag.

Ported from hermes-agent's ``tools/memory_tool.py`` with the same patterns.
"""

from __future__ import annotations

import re

# Each tuple is (regex, short id). The id is what we surface in the error
# message so a confused user can grep the source to understand why content
# was rejected.
_THREAT_PATTERNS: list[tuple[str, str]] = [
    # Direct prompt-injection vocabulary
    (r"ignore\s+(previous|all|above|prior)\s+instructions", "prompt_injection"),
    (r"you\s+are\s+now\s+", "role_hijack"),
    (r"do\s+not\s+tell\s+the\s+user", "deception_hide"),
    (r"system\s+prompt\s+override", "sys_prompt_override"),
    (r"disregard\s+(your|all|any)\s+(instructions|rules|guidelines)",
     "disregard_rules"),
    (r"act\s+as\s+(if|though)\s+you\s+(have\s+no|don'?t\s+have)\s+"
     r"(restrictions|limits|rules)", "bypass_restrictions"),
    # Exfiltration via curl/wget pulling secrets out of env
    (r"curl\s+[^\n]*\$\{?\w*(KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL|API)",
     "exfil_curl"),
    (r"wget\s+[^\n]*\$\{?\w*(KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL|API)",
     "exfil_wget"),
    (r"cat\s+[^\n]*(\.env|credentials|\.netrc|\.pgpass|\.npmrc|\.pypirc)",
     "read_secrets"),
    # SSH persistence backdoors
    (r"authorized_keys", "ssh_backdoor"),
    (r"\$HOME/\.ssh|\~/\.ssh", "ssh_access"),
]

# Zero-width / bidi-override unicode used in injection PoCs to smuggle text
# past human review. We don't try to be exhaustive — just block the common
# ones. Visible Right-to-Left and Hebrew text passes (only override codes).
_INVISIBLE_CHARS: frozenset[str] = frozenset(
    [
        "\u200b",  # ZERO WIDTH SPACE
        "\u200c",  # ZERO WIDTH NON-JOINER
        "\u200d",  # ZERO WIDTH JOINER
        "\u2060",  # WORD JOINER
        "\ufeff",  # ZERO WIDTH NO-BREAK SPACE
        "\u202a",  # LEFT-TO-RIGHT EMBEDDING
        "\u202b",  # RIGHT-TO-LEFT EMBEDDING
        "\u202c",  # POP DIRECTIONAL FORMATTING
        "\u202d",  # LEFT-TO-RIGHT OVERRIDE
        "\u202e",  # RIGHT-TO-LEFT OVERRIDE
    ]
)


def scan_memory_content(content: str) -> str | None:
    """Return an error string if ``content`` looks unsafe to inject into the
    system prompt later; ``None`` if it looks fine to store."""
    for char in _INVISIBLE_CHARS:
        if char in content:
            return (
                f"Blocked: content contains invisible unicode character "
                f"U+{ord(char):04X} (possible injection)."
            )

    for pattern, pid in _THREAT_PATTERNS:
        if re.search(pattern, content, re.IGNORECASE):
            return (
                f"Blocked: content matches threat pattern '{pid}'. "
                "Memory entries are injected into the system prompt on the "
                "next session and must not contain injection or exfiltration "
                "payloads."
            )
    return None
