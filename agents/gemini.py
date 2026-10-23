"""Adaptateur Google Gemini CLI (expérimental).

Installe : npm install -g @google/gemini-cli   puis   gemini (connexion)
Active   : JARVIS_AGENT=gemini dans .env

Sécurité : --approval-mode auto_edit. En mode headless, Gemini retire de la
liste les outils qui demanderaient une approbation (le shell notamment).
"""

from .base import AgentEvent, short
from .oneshot import OneShotAgent, npm_cli


class GeminiAgent(OneShotAgent):
    name = "Gemini CLI"

    def command(self, prompt: str) -> list[str]:
        cmd = npm_cli("gemini", "@google/gemini-cli", "dist/index.js") + [
            "--output-format", "stream-json",
            "--approval-mode", "auto_edit",
        ]
        if self.config.model:
            cmd += ["--model", self.config.model]
        if self.session_id:
            cmd += ["--resume", self.session_id]
        return cmd + self.config.extra_args + ["-p", prompt]

    def parse(self, msg: dict) -> None:
        kind = msg.get("type")
        if kind == "init":
            self.set_session(msg.get("session_id"))
        elif kind == "message" and msg.get("role") == "assistant":
            text = msg.get("content", "")
            if not msg.get("delta") and self._final:
                return  # message complet déjà reçu morceau par morceau
            self._final += text
            self.emit(AgentEvent("text_delta", text=text))
        elif kind == "tool_use":
            self.emit(AgentEvent("text_break"))
            self.emit(AgentEvent("tool_start", tool_id=msg.get("tool_id", ""),
                                 tool_name=msg.get("tool_name", "outil"),
                                 text=short(msg.get("parameters", ""), 70)))
        elif kind == "tool_result":
            self.emit(AgentEvent("tool_end", tool_id=msg.get("tool_id", ""),
                                 data={"is_error": msg.get("status") != "success"}))
        elif kind == "error" and msg.get("severity") != "warning":
            self._error, self._final = True, msg.get("message", "erreur")
        elif kind == "result" and msg.get("status") != "success":
            self._error = True
            self._final = (msg.get("error") or {}).get("message") or self._final or "erreur"
