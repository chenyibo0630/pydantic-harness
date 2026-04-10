"""Skill loader — scan skills/ directory for SKILL.md + config.yaml.

Each skill directory contains:
  SKILL.md      — frontmatter (name, description) + workflow docs
  config.yaml   — skill-specific configuration (API keys, provider, etc.)

A skill is loaded only if config.yaml exists (indicates it's configured).
Config values with '_key' or '_secret' suffix are injected as uppercase
env vars scoped to the skill's subprocess execution.
"""

import logging
from pathlib import Path

import yaml

from backend.core.skills.types import SkillInfo

logger = logging.getLogger(__name__)


def _parse_frontmatter(text: str) -> dict[str, str]:
    """Extract YAML frontmatter from SKILL.md."""
    if not text.startswith("---"):
        return {}
    end = text.find("---", 3)
    if end == -1:
        return {}
    block = text[3:end].strip()
    result: dict[str, str] = {}
    for line in block.splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            result[key.strip()] = value.strip()
    return result


def _load_skill_config(skill_dir: Path) -> dict | None:
    """Load config.yaml from skill directory. Returns None if not found."""
    config_file = skill_dir / "config.yaml"
    if not config_file.exists():
        return None
    try:
        return yaml.safe_load(config_file.read_text(encoding="utf-8")) or {}
    except Exception:
        logger.exception("Failed to parse config.yaml in %s", skill_dir.name)
        return None


def load_skills(skills_dir: str | Path, enabled: list[str] | None = None) -> list[SkillInfo]:
    """Scan skills directory for configured skill packages.

    A skill is loaded when:
    1. Has SKILL.md with name + description
    2. Has config.yaml (skill-level configuration)
    3. Name is in the enabled list (if provided)
    """
    root = Path(skills_dir)
    if not root.is_dir():
        logger.warning("Skills directory not found: %s", root)
        return []

    skills: list[SkillInfo] = []
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        skill_file = child / "SKILL.md"
        if not skill_file.exists():
            continue

        config = _load_skill_config(child) or {}

        try:
            text = skill_file.read_text(encoding="utf-8")
            fm = _parse_frontmatter(text)
            name = fm.get("name")
            desc = fm.get("description")
            if not name or not desc:
                logger.debug("Skipping %s — missing name or description", child.name)
                continue
            if enabled is not None and name not in enabled:
                logger.debug("Skipping %s — not in enabled list", name)
                continue

            skills.append(SkillInfo(
                name=name,
                description=desc,
                skill_dir=child,
                skill_file=skill_file,
                config=config,
            ))
            logger.info("Loaded skill: %s", name)
        except Exception:
            logger.exception("Failed to parse skill: %s", child.name)

    return skills
