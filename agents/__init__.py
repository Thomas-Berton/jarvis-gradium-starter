"""Les agents que Jarvis sait piloter. Choix avec JARVIS_AGENT dans .env."""

from .base import Agent, AgentConfig, AgentEvent
from .claude_code import ClaudeCodeAgent
from .codex import CodexAgent
from .gemini import GeminiAgent

AGENTS: dict[str, type[Agent]] = {
    "claude": ClaudeCodeAgent,
    "codex": CodexAgent,
    "gemini": GeminiAgent,
}


def create_agent(name: str, config: AgentConfig) -> Agent:
    try:
        return AGENTS[name.lower()](config)
    except KeyError:
        raise ValueError(
            f"Agent inconnu : {name!r}. Choix possibles : {', '.join(AGENTS)}"
        ) from None


__all__ = ["Agent", "AgentConfig", "AgentEvent", "AGENTS", "create_agent"]
