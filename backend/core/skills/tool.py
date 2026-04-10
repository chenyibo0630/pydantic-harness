"""read_skill tool — lazy-loads full SKILL.md content when agent needs it.

At startup, only skill name/description are injected into the system prompt.
When the agent decides to use a skill, it calls read_skill(name) to get
the full workflow instructions.
"""

import logging

from backend.core.skills.types import SkillInfo

logger = logging.getLogger(__name__)

_skills_index: dict[str, SkillInfo] = {}


def init_skill_tool(skills: list[SkillInfo]) -> None:
    """Build the name → SkillInfo index. Called at startup."""
    _skills_index.clear()
    for s in skills:
        _skills_index[s.name] = s


def read_skill(name: str) -> str:
    """Load a skill's full instructions by name.

    Call this when a user request matches a skill listed in <available_skills>.
    Returns the complete SKILL.md content with workflow steps.

    If the skill has scripts, {SCRIPTS_DIR} placeholders are replaced with
    the actual scripts directory path.

    Args:
        name: The skill name from <available_skills>.
    """
    skill = _skills_index.get(name)
    if skill is None:
        available = ", ".join(_skills_index.keys()) or "(none)"
        return f"Skill '{name}' not found. Available: {available}"

    content = skill.skill_file.read_text(encoding="utf-8")

    # Replace {SCRIPTS_DIR} with actual path
    scripts_dir = skill.skill_dir / "scripts"
    if scripts_dir.is_dir():
        content = content.replace("{SCRIPTS_DIR}", str(scripts_dir.resolve()))

    return content
