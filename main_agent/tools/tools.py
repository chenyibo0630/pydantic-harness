"""Main Agent tool configuration.

Each agent explicitly picks which tools it needs from backend.core.sandbox.
"""

import importlib
import logging
import pkgutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

from backend.core.sandbox import (
    bash_execute,
    read_file,
    list_dir,
    str_replace,
    write_file,
    glob_files,
    grep_search,
)
from backend.core.tools import ask_user

logger = logging.getLogger(__name__)

DEFAULT_TOOLS: list[Callable[..., Any]] = [
    bash_execute,
    read_file,
    list_dir,
    str_replace,
    write_file,
    glob_files,
    grep_search,
    ask_user,
]


def get_available_tools() -> list[Callable[..., Any]]:
    return DEFAULT_TOOLS
