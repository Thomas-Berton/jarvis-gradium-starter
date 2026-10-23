"""Adaptateur OpenAI Codex CLI (expérimental).

Installe : npm install -g @openai/codex   puis   codex login
Active   : JARVIS_AGENT=codex dans .env

Sécurité : sandbox « workspace-write » (écriture seulement dans le workspace)
et aucune demande d'approbation (approval_policy=never) : ce que la sandbox
interdit est simplement refusé.
"""

from .base import AgentEvent, short
from .oneshot import OneShotAgent, npm_cli


class CodexAgent(OneShotAgent):
    name = "Codex"

    def command(self, prompt: str) -> list[str]:
        cmd = npm_cli("codex", "@openai/codex", "bin/codex.js") + [
            "exec", "--json",
            "--sandbox", "workspace-write",
            "--skip-git-repo-check",
            "-c", 'approval_policy="never"',
        ]
        if self.config.model:
            cmd += ["--model", self.config.model]
        cmd += self.config.extra_args
        if self.session_id:
            cmd += ["resume", self.session_id]
        return cmd + [prompt]

    def parse(self, msg: dict) -> None:
        kind = msg.get("type")
        item = msg.get("item") or {}
        itype = item.get("type")
        if kind == "thread.started":
            self.set_session(msg.get("thread_id"))
        elif kind == "item.completed" and itype == "agent_message":
            # Codex ne streame pas mot à mot : on reçoit le message entier.
            text = item.get("text", "")
            self._final = text
            self.emit(AgentEvent("text_delta", text=text))
            self.emit(AgentEvent("text_break"))
        elif kind == "item.started" and itype not in ("agent_message", "reasoning"):
            label = item.get("command") or item.get("query") or item.get("tool") or ""
            self.emit(AgentEvent("tool_start", tool_id=item.get("id", ""),
                                 tool_name=itype or "outil", text=short(label, 70)))
        elif kind == "item.completed" and itype not in ("agent_message", "reasoning"):
            self.emit(AgentEvent("tool_end", tool_id=item.get("id", ""),
                                 data={"is_error": item.get("status") in ("failed", "declined")}))
        elif kind in ("turn.failed", "error"):
            self._error = True
            self._final = (msg.get("error") or {}).get("message") or msg.get("message", "erreur")
