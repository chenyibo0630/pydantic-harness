"""MainAgent — primary orchestrating agent.

The agent's ``instructions`` is a callable that returns
``ctx.deps.system_prompt`` — a per-conversation frozen snapshot the gateway
resolves at the start of each turn. This pattern locks the system message
for the lifetime of a conversation: editing prompt files on disk affects
only **new** conversations, not in-progress ones. See
``build_system_prompt`` for how the snapshot text is assembled.
"""

from pydantic_ai import Agent, RunContext
from pydantic_ai.settings import ModelSettings

from backend.core.llm import LLMConfig, build_model
from backend.core.conversation import ConversationDeps
from backend.core.skills import SkillInfo, build_skills_prompt
from backend.core.tools import get_memory_store
from main_agent.config import Settings
from main_agent.tools.tools import get_available_tools


def _build_model_settings(llm: LLMConfig) -> ModelSettings:
    ms = ModelSettings(max_tokens=llm.max_tokens, temperature=llm.temperature)
    if llm.thinking is not None:
        ms["thinking"] = llm.thinking

    # Anthropic prompt-cache the two stable parts of every request:
    #   - tool definitions (~3000 tokens, identical across the process lifetime)
    #   - system instructions (~800 tokens, locked per conversation by deps)
    # Cached at the 5-minute TTL price tier, ~90% off on cache hits.
    # Non-Anthropic providers silently ignore these keys.
    if llm.type == "anthropic":
        ms["anthropic_cache_tool_definitions"] = True  # type: ignore[typeddict-unknown-key]
        ms["anthropic_cache_instructions"] = True  # type: ignore[typeddict-unknown-key]

    return ms


def build_system_prompt(
    settings: Settings, skills: list[SkillInfo] | None = None
) -> str:
    """Compose the full system prompt by reading on-disk prompt files and
    appending the skills section + curated notes (MEMORY.md / USER.md).

    Called per **new** conversation (not per turn). The notes section is
    frozen here too: mid-conversation writes via the ``memory`` tool only
    land on disk and won't change this conversation's prompt — the next
    one will see them."""
    instructions = settings.system_prompt
    if skills:
        skills_prompt = build_skills_prompt(skills)
        if skills_prompt:
            instructions = f"{instructions}\n\n{skills_prompt}"

    # Append durable curated notes if the store is initialized. USER goes
    # before MEMORY so the user-profile context frames the agent's notes.
    store = get_memory_store()
    if store is not None:
        blocks = [
            b for b in (store.render_system_block("user"),
                        store.render_system_block("memory"))
            if b
        ]
        if blocks:
            instructions = f"{instructions}\n\n" + "\n\n".join(blocks)

    return instructions


def _session_instructions(ctx: RunContext[ConversationDeps]) -> str:
    """pydantic-ai callable instructions — every LLM call within a turn
    reads the same snapshot from deps, so the system message is byte-stable
    across the entire conversation."""
    return ctx.deps.system_prompt


def create_agent(
    settings: Settings, skills: list[SkillInfo] | None = None
) -> Agent[ConversationDeps, str]:
    """Construct the main agent.

    ``skills`` is no longer baked into the agent's instructions here; it
    flows in per-request via ``ConversationDeps.system_prompt`` (resolved by the
    gateway). The ``skills`` argument is retained for API symmetry with
    legacy callers but has no effect on the agent itself.
    """
    del skills  # snapshot resolution happens at request time, not here

    model = build_model(settings.llm)
    return Agent(
        model=model,
        deps_type=ConversationDeps,
        instructions=_session_instructions,
        model_settings=_build_model_settings(settings.llm),
        tools=get_available_tools(),
        name="bob-harness",
    )
