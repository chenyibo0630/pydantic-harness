from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class SkillInfo:
    name: str
    description: str
    skill_dir: Path
    skill_file: Path
    config: dict = field(default_factory=dict)
