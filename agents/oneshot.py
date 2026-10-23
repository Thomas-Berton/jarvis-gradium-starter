"""Base pour les agents qui lancent un processus par tour (Codex, Gemini...).

Chaque phrase lance la commande en mode headless avec sortie JSON ligne par
ligne, puis reprend la session précédente (mémoire). Les messages qui
arrivent pendant qu'un tour tourne sont mis en file d'attente.

Pour ajouter un CLI : hérite de OneShotAgent et implémente command() et
parse(). Voir codex.py et gemini.py.
"""

import asyncio
import json
import os
import pathlib

from .base import Agent, AgentEvent, find_executable, kill_tree, popen_kwargs, short


def npm_cli(name: str, package: str, entry: str) -> list[str]:
    """Commande pour un CLI installé avec npm, sans passer par cmd.exe sous Windows."""
    found = find_executable(name)
    if not found:
        raise FileNotFoundError(f"Commande '{name}' introuvable (npm install -g {package}).")
    if os.name == "nt" and found.lower().endswith((".cmd", ".bat", ".ps1")):
        script = pathlib.Path(found).parent / "node_modules" / package / entry
        node = find_executable("node")
        if script.exists() and node:
            return [node, str(script)]
    return [found]


class OneShotAgent(Agent):
    def __init__(self, config):
        super().__init__(config)
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._worker: asyncio.Task | None = None
        self._proc: asyncio.subprocess.Process | None = None

    # -- à implémenter par chaque CLI
    def command(self, prompt: str) -> list[str]:
        raise NotImplementedError

    def parse(self, msg: dict) -> None:
        """Transforme une ligne JSON du CLI en AgentEvent (self.emit)."""
        raise NotImplementedError

    # -- commun
    def prompt_for(self, text: str) -> str:
        # Sans option « system prompt », on donne la personnalité au premier message.
        if self.session_id:
            return text
        return f"{self.config.system_prompt}\n\n---\n\nDemande de l'utilisateur : {text}"

    async def start(self) -> None:
        self._worker = asyncio.create_task(self._work())

    async def send(self, text: str) -> None:
        self.busy = True
        await self._queue.put(text)

    async def interrupt(self) -> None:
        while not self._queue.empty():
            self._queue.get_nowait()
        if self._proc:
            kill_tree(self._proc)

    async def stop(self) -> None:
        await self.interrupt()
        if self._worker:
            self._worker.cancel()

    async def _work(self) -> None:
        while True:
            text = await self._queue.get()
            self.busy = True
            self.emit(AgentEvent("turn_start", text=text))
            self._final, self._error = "", False
            try:
                await self._run(self.prompt_for(text))
            except Exception as exc:
                self._final, self._error = str(exc), True
            self.emit(AgentEvent("text_break"))
            self.emit(AgentEvent("turn_done", text=self._final, data={"is_error": self._error}))
            self.busy = not self._queue.empty()

    async def _run(self, prompt: str) -> None:
        self._proc = proc = await asyncio.create_subprocess_exec(
            *self.command(prompt),
            cwd=self.config.workdir,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=16 * 1024 * 1024,
            **popen_kwargs(),
        )
        stderr_task = asyncio.create_task(proc.stderr.read())
        other = []  # lignes non JSON : souvent un message d'erreur ou de connexion
        try:
            while line := await proc.stdout.readline():
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    other.append(line.decode(errors="replace").strip())
                    continue
                if isinstance(msg, dict):
                    self.parse(msg)
            code = await proc.wait()
        finally:
            self._proc = None
        err = (await stderr_task).decode(errors="replace").strip()
        if code != 0 and not self._error:
            self._error = True
            self._final = short(err or " ".join(other) or f"code de sortie {code}", 400)
        elif not self._final and not self._error:
            # Rien reçu : CLI pas connecté, mauvaise option...
            self._error = True
            self._final = short(" ".join(other) or err or "Aucune réponse de l'agent.", 400)

    def set_session(self, session_id: str | None) -> None:
        if session_id and session_id != self.session_id:
            self.session_id = session_id
            self.emit(AgentEvent("session", data={"session_id": session_id}))
