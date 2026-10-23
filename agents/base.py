"""Interface commune à tous les agents (Claude Code, Codex, Gemini...).

Un agent reçoit le texte que tu as dit et produit un flux d'événements :
le texte de sa réponse (lu à voix haute), les outils qu'il utilise (affichés
dans le panneau), la fin du tour, les erreurs.

Pour brancher un autre agent : hérite de Agent, implémente start / send /
interrupt / stop, et pousse des AgentEvent avec self.emit(...).
"""

import asyncio
import os
import pathlib
import shutil
import signal
import subprocess
from dataclasses import dataclass, field
from typing import AsyncIterator, Literal

EventKind = Literal[
    "session",             # data["session_id"] : identifiant de session de l'agent
    "turn_start",          # text = ce que l'utilisateur a demandé
    "text_delta",          # text = morceau de réponse, à lire à voix haute
    "text_break",          # fin d'un bloc de texte (avant un outil, fin du tour)
    "tool_start",          # tool_id, tool_name, text = résumé lisible
    "tool_end",            # tool_id, data["is_error"]
    "permission_request",  # tool_id = id de la demande, tool_name, text = résumé
    "turn_done",           # data["is_error"], text = résultat final
    "error",               # text = message d'erreur
]


@dataclass
class AgentEvent:
    kind: EventKind
    text: str = ""
    tool_id: str = ""
    tool_name: str = ""
    data: dict = field(default_factory=dict)


@dataclass
class AgentConfig:
    workdir: pathlib.Path
    system_prompt: str            # personnalité de Jarvis (jarvis.md)
    model: str | None = None      # None = modèle par défaut du CLI
    resume: str | None = None     # id de session à reprendre (mémoire)
    extra_args: list[str] = field(default_factory=list)


class Agent:
    """Classe de base. Les événements sont lus avec `async for ev in agent.events()`."""

    name = "agent"

    def __init__(self, config: AgentConfig):
        self.config = config
        self.session_id: str | None = config.resume
        self.busy = False
        self._events: asyncio.Queue[AgentEvent] = asyncio.Queue()

    # -- à implémenter
    async def start(self) -> None: ...
    async def send(self, text: str) -> None: ...
    async def interrupt(self) -> None: ...
    async def stop(self) -> None: ...

    async def respond_permission(self, request_id: str, allow: bool) -> None:
        """Réponse à une permission_request. Seuls certains agents en émettent."""

    # -- commun
    def emit(self, ev: AgentEvent) -> None:
        self._events.put_nowait(ev)

    async def events(self) -> AsyncIterator[AgentEvent]:
        while True:
            yield await self._events.get()


# ------------------------------------------------------------------ outils

def find_executable(name: str) -> str | None:
    return shutil.which(name)


def popen_kwargs() -> dict:
    """Groupe de processus à part, pour pouvoir tout tuer d'un coup."""
    if os.name == "posix":
        return {"start_new_session": True}
    return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}


def kill_tree(proc: asyncio.subprocess.Process) -> None:
    """Tue le processus ET ses enfants (outils, serveurs MCP...)."""
    if proc.returncode is not None:
        return
    try:
        if os.name == "posix":
            os.killpg(proc.pid, signal.SIGKILL)
        else:
            subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                capture_output=True,
            )
    except (ProcessLookupError, PermissionError):
        pass


def short(text: str, n: int = 90) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[: n - 1] + "…"
