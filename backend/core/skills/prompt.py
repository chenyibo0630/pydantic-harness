"""Build the skills section for agent system prompt."""

from backend.core.skills.types import SkillInfo


def build_skills_prompt(skills: list[SkillInfo]) -> str:
    """Generate the <skill_system> prompt section.

    Only name + description + has_scripts are injected.
    Agent calls read_skill(name) to load full instructions.
    """
    if not skills:
        return ""

    entries = []
    for s in skills:
        scripts_dir = s.skill_dir / "scripts"
        has_scripts = scripts_dir.is_dir() and any(scripts_dir.iterdir())
        entries.append(
            f"  <skill>\n"
            f"    <name>{s.name}</name>\n"
            f"    <description>{s.description}</description>\n"
            f"    <has_scripts>{has_scripts}</has_scripts>\n"
            f"  </skill>"
        )

    skills_xml = "\n".join(entries)

    return (
        "<skill_system>\n"
        "When a user request matches a skill's description, call read_skill(name) to load it, "
        "then follow the workflow inside.\n"
        "If has_scripts=True, use bash_execute to run the skill's scripts as instructed.\n\n"
        "<available_skills>\n"
        f"{skills_xml}\n"
        "</available_skills>\n"
        "</skill_system>"
    )
