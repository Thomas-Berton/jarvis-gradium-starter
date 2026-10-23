"""Adaptateur Claude Code.

Un seul processus `claude` reste ouvert pendant toute la vie du serveur
(mode stream-json sur stdin/stdout). Avantages :
- pas de temps de démarrage à chaque phrase,
- Claude se souvient de toute la conversation,
- on peut l'interrompre sans le tuer, et lui parler pendant qu'il travaille
  (les messages sont mis en file d'attente).

Sécurité : chaque outil qui demanderait une autorisation dans Claude Code
(commande shell, fichier hors du workspace, outil MCP...) déclenche une
permission_request. Jarvis te la montre dans le panneau, tu acceptes ou refuses.
"""

import asyncio
import json
import os
import pathlib
import uuid

from .base import Agent, AgentEvent, find_executable, kill_tree, popen_kwargs, short

ROOT = pathlib.Path(__file__).resolve().parent.parent

# Outils autorisés sans demander. Les modifications de fichiers DANS le
# workspace sont aussi autorisées (--permission-mode acceptEdits).
# Tout le reste passe par une demande d'autorisation dans le panneau.
AUTO_ALLOWED_TOOLS = ["WebSearch", "WebFetch"]


def claude_command() -> list[str]:
    """Chemin vers le vrai binaire Claude Code.

    Sous Windows, `claude` est un script .cmd : passer par lui ferait passer
    le texte par cmd.exe (texte tronqué, caractères spéciaux interprétés).
    On lance donc directement claude.exe.
    """
    custom = os.environ.get("JARVIS_CLAUDE_BIN")
    if custom:
        return [custom]
    found = find_executable("claude")
    if not found:
        raise FileNotFoundError(
            "Claude Code introuvable. Installe-le : npm install -g @anthropic-ai/claude-code"
        )
    if os.name == "nt" and found.lower().endswith((".cmd", ".bat", ".ps1")):
        pkg = pathlib.Path(found).parent / "node_modules" / "@anthropic-ai" / "claude-code"
        exe = pkg / "bin" / "claude.exe"
        if exe.exists():
            return [str(exe)]
        cli = pkg / "cli.js"  # anciennes versions, distribuées en JavaScript
        node = find_executable("node")
        if cli.exists() and node:
            return [node, str(cli)]
    return [found]


def tool_summary(name: str, args: dict) -> str:
    """Une ligne lisible pour le panneau : « Write idees.md », « Bash ls -la »..."""
    for key in ("file_path", "path", "notebook_path"):
        if key in args:
            return pathlib.Path(str(args[key])).name
    for key in ("command", "query", "url", "pattern", "description", "prompt"):
        if key in args:
            return short(args[key], 70)
    return ""


class ClaudeCodeAgent(Agent):
    name = "Claude Code"

    def __init__(self, config):
        super().__init__(config)
        self.proc: asyncio.subprocess.Process | None = None
        self._reader: asyncio.Task | None = None
        self._pending = 0                       # messages envoyés, pas encore terminés
        self._inflight: list[str] = []          # leur texte (pour les renvoyer si besoin)
        self._blocks: dict[int, str] = {}       # index -> type du bloc en cours
        self._permissions: dict[str, dict] = {}  # request_id -> input de l'outil
        self._lock = asyncio.Lock()
        self._initialized = False               # le processus a-t-il démarré correctement ?

    # ------------------------------------------------------------ cycle de vie

    def _args(self) -> list[str]:
        cfg = self.config
        args = claude_command() + [
            "-p",
            "--input-format", "stream-json",
            "--output-format", "stream-json",
            "--verbose",
            "--include-partial-messages",
            # Les demandes d'autorisation arrivent sur stdout : Jarvis te les montre.
            "--permission-prompt-tool", "stdio",
            "--permission-mode", "acceptEdits",
            "--allowedTools", ",".join(AUTO_ALLOWED_TOOLS),
            # Seuls les réglages du projet (workspace/.claude/settings.json) sont
            # lus : tes hooks et réglages globaux ne s'appliquent pas à Jarvis.
            "--setting-sources", "project",
            # Seuls les serveurs MCP de mcp.json sont chargés.
            "--strict-mcp-config", "--mcp-config", str(ROOT / "mcp.json"),
            "--append-system-prompt", cfg.system_prompt,
        ]
        if cfg.model:
            args += ["--model", cfg.model]
        if self.session_id:
            args += ["--resume", self.session_id]
        return args + cfg.extra_args

    async def start(self) -> None:
        self._initialized = False
        self.proc = await asyncio.create_subprocess_exec(
            *self._args(),
            cwd=self.config.workdir,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=16 * 1024 * 1024,  # certaines lignes JSON sont très longues
            **popen_kwargs(),
        )
        self._reader = asyncio.create_task(self._read_loop(self.proc))

    async def stop(self) -> None:
        proc, self.proc = self.proc, None
        if proc:
            kill_tree(proc)
            await proc.wait()
        if self._reader:
            self._reader.cancel()
        self._pending, self.busy = 0, False

    async def _write(self, obj: dict) -> None:
        async with self._lock:
            if not self.proc or self.proc.returncode is not None:
                await self.start()
            self.proc.stdin.write((json.dumps(obj) + "\n").encode())
            await self.proc.stdin.drain()

    # ------------------------------------------------------------ actions

    async def send(self, text: str) -> None:
        self.emit(AgentEvent("turn_start", text=text))
        await self._send_user(text)

    async def _send_user(self, text: str) -> None:
        self._pending += 1
        self._inflight.append(text)
        self.busy = True
        await self._write({
            "type": "user",
            "message": {"role": "user", "content": [{"type": "text", "text": text}]},
            "parent_tool_use_id": None,
            "session_id": self.session_id or "",
        })

    async def interrupt(self) -> None:
        if self.busy and self.proc and self.proc.returncode is None:
            await self._write({
                "type": "control_request",
                "request_id": f"int_{uuid.uuid4().hex[:8]}",
                "request": {"subtype": "interrupt"},
            })

    async def respond_permission(self, request_id: str, allow: bool) -> None:
        tool_input = self._permissions.pop(request_id, None)
        if tool_input is None:
            return
        if allow:
            decision = {"behavior": "allow", "updatedInput": tool_input}
        else:
            decision = {"behavior": "deny", "message": "L'utilisateur a refusé."}
        await self._write({
            "type": "control_response",
            "response": {"subtype": "success", "request_id": request_id, "response": decision},
        })

    # ------------------------------------------------------------ lecture

    async def _read_loop(self, proc) -> None:
        stderr_task = asyncio.create_task(proc.stderr.read())
        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            try:
                self._handle(msg)
            except Exception as exc:  # un message inattendu ne doit pas tout casser
                self.emit(AgentEvent("error", text=f"Message Claude illisible : {exc}"))
        await proc.wait()
        err = (await stderr_task).decode(errors="replace").strip()
        if proc is self.proc:  # mort inattendue (pas un stop() volontaire)
            self.proc = None
            if not self._initialized and self.session_id:
                # La conversation à reprendre n'existe plus (historique effacé...) :
                # on en démarre une nouvelle et on renvoie les messages en attente.
                self.session_id = None
                retry, self._inflight, self._pending = self._inflight, [], 0
                for text in retry:
                    await self._send_user(text)
                return
            self._inflight.clear()
            if self._pending:
                self._pending, self.busy = 0, False
                self.emit(AgentEvent(
                    "turn_done", text=short(err or "Claude Code s'est arrêté.", 400),
                    data={"is_error": True},
                ))
            elif err:
                self.emit(AgentEvent("error", text=short(err, 400)))

    def _handle(self, msg: dict) -> None:
        kind = msg.get("type")

        if kind == "system" and msg.get("subtype") == "init":
            self._initialized = True
            if msg.get("session_id") and msg["session_id"] != self.session_id:
                self.session_id = msg["session_id"]
                self.emit(AgentEvent("session", data={"session_id": self.session_id}))

        elif kind == "stream_event":
            if msg.get("parent_tool_use_id"):
                return  # texte d'un sous-agent : pas lu à voix haute
            ev = msg.get("event", {})
            etype, idx = ev.get("type"), ev.get("index", 0)
            if etype == "content_block_start":
                self._blocks[idx] = ev.get("content_block", {}).get("type", "")
            elif etype == "content_block_delta":
                delta = ev.get("delta", {})
                if delta.get("type") == "text_delta" and delta.get("text"):
                    self.emit(AgentEvent("text_delta", text=delta["text"]))
            elif etype == "content_block_stop":
                if self._blocks.pop(idx, "") == "text":
                    self.emit(AgentEvent("text_break"))

        elif kind == "assistant":
            for block in msg.get("message", {}).get("content", []):
                if block.get("type") == "tool_use":
                    self.emit(AgentEvent(
                        "tool_start", tool_id=block.get("id", ""),
                        tool_name=block.get("name", "?"),
                        text=tool_summary(block.get("name", ""), block.get("input") or {}),
                    ))

        elif kind == "user":
            content = msg.get("message", {}).get("content", [])
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_result":
                        self.emit(AgentEvent(
                            "tool_end", tool_id=block.get("tool_use_id", ""),
                            data={"is_error": bool(block.get("is_error"))},
                        ))

        elif kind == "control_request":
            req = msg.get("request", {})
            if req.get("subtype") == "can_use_tool":
                rid = msg.get("request_id", "")
                self._permissions[rid] = req.get("input") or {}
                name = req.get("tool_name", "?")
                self.emit(AgentEvent(
                    "permission_request", tool_id=rid, tool_name=name,
                    text=tool_summary(name, req.get("input") or {}),
                    data={"input": req.get("input") or {}},
                ))

        elif kind == "result":
            if not self._initialized and self.session_id:
                return  # échec de reprise de conversation : géré à l'arrêt du processus
            if self._inflight:
                self._inflight.pop(0)
            self._pending = max(0, self._pending - 1)
            self.busy = self._pending > 0
            self._blocks.clear()
            is_error = bool(msg.get("is_error"))
            text = msg.get("result") or ""
            if msg.get("terminal_reason") == "aborted_streaming":
                text = "Tâche interrompue."
            elif is_error and not text:
                text = "; ".join(msg.get("errors") or []) or msg.get("subtype", "erreur")
            self.emit(AgentEvent("turn_done", text=text, data={
                "is_error": is_error,
                "cost_usd": msg.get("total_cost_usd"),
                "duration_ms": msg.get("duration_ms"),
            }))
