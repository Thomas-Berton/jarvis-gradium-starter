#!/usr/bin/env python3
"""JARVIS x Gradium : kit de départ du hackathon Projet Jarvis.

Tu parles -> Gradium transcrit -> Claude Code (en fond, sur ta machine) agit
et répond -> Gradium lit sa réponse à voix haute.

- La voix (écoute, fin de phrase, parole) : Gradium        -> voice.py
- Le cerveau et les mains : Claude Code (ou Codex, Gemini) -> agents/
- La personnalité de Jarvis                                -> jarvis.md
- L'interface (orbe + activité de l'agent)                 -> static/

Lancer :  python main.py   puis ouvrir http://127.0.0.1:8000
"""

import asyncio
import contextlib
import json
import logging
import os
import pathlib
import sys
import time
import unicodedata

from dotenv import load_dotenv

# Console Windows : évite un plantage d'affichage sur un caractère exotique.
for stream in (sys.stdout, sys.stderr):
    stream.reconfigure(errors="replace")

ROOT = pathlib.Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

import fastapi  # noqa: E402
import gradium  # noqa: E402
from fastapi.responses import FileResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402

from agents import AgentConfig, AgentEvent, create_agent  # noqa: E402
from voice import SAMPLE_RATE, Listener, Speaker  # noqa: E402

# ------------------------------------------------------------------ config

GRADIUM_API_KEY = os.environ.get("GRADIUM_API_KEY", "").strip()
GRADIUM_BASE_URL = os.environ.get("GRADIUM_BASE_URL", "https://api.gradium.ai/api/")
AGENT_NAME = os.environ.get("JARVIS_AGENT", "claude")
AGENT_MODEL = os.environ.get("JARVIS_MODEL") or None
VOICE = os.environ.get("JARVIS_VOICE", "Marius")
WORKDIR = pathlib.Path(
    os.path.expanduser(os.environ.get("JARVIS_WORKDIR", str(ROOT / "workspace")))
).resolve()
GREETING = os.environ.get("JARVIS_GREETING", "Bonjour, je t'écoute.")
END_OF_TURN = float(os.environ.get("JARVIS_END_OF_TURN", "0.8"))
BARGE_IN = os.environ.get("JARVIS_BARGE_IN", "true").lower() == "true"
PERMISSION_TIMEOUT = float(os.environ.get("JARVIS_PERMISSION_TIMEOUT", "120"))
KEYWORDS = [w for w in os.environ.get("JARVIS_KEYWORDS", "Jarvis").split(",") if w.strip()]
PORT = int(os.environ.get("JARVIS_PORT", "8000"))
DEBUG = os.environ.get("DEBUG", "false").lower() == "true"

# Phrases qui arrêtent la tâche en cours au lieu d'être envoyées à l'agent.
STOP_WORDS = {"stop", "stoppe", "arrete", "arrete tout", "annule", "annule tout",
              "tais toi", "stop jarvis", "jarvis stop", "laisse tomber"}

# Voix françaises « flagship » de Gradium. Une voix clonée ou créée dans
# Gradium Studio marche aussi : mets son voice_id dans JARVIS_VOICE.
VOICES = {
    "Marius": "biuhvu17TxVKOcyy", "Jules": "YKeBw3OV1RgpdhLh",
    "Gaspard": "iEu63s1rhn_kegTr", "Damien": "25AzBFyp6svYnJsj",
    "Augustin": "Tek4tJXiX6_yvXq7", "Solène": "YhIHaAfQ0cQPDV9R",
    "Noémie": "FXxJ9mANRq6BCTX5", "Maëlys": "s048cR1l2Jmu4k3B",
    "Coralie": "ZeSg853xFACESHHI", "Apolline": "6oIkS98REoVZ1dEw",
    "Mélanie (Québec)": "xynYWquoAsrvM7UY", "Maude (Québec)": "sBLwTd5womVX8JOw",
}


def resolve_voice(name_or_id: str | None) -> str:
    if not name_or_id:
        return VOICES["Marius"]
    for name, vid in VOICES.items():
        if name_or_id.lower() in (name.lower(), name.split(" ")[0].lower(), vid.lower()):
            return vid
    return name_or_id


logging.basicConfig(level=logging.DEBUG if DEBUG else logging.INFO,
                    format="%(asctime)s %(name)s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("jarvis")
WORKDIR.mkdir(parents=True, exist_ok=True)
STATE_FILE = ROOT / ".jarvis-state.json"   # id de session de l'agent (mémoire)


def load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text("utf-8"))
    except (OSError, ValueError):
        return {}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state), "utf-8")


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFD", text.lower())
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    return " ".join("".join(c if c.isalnum() else " " for c in text).split())

# ------------------------------------------------------------------ le hub

class Hub:
    """Garde l'agent en vie (il survit à un rechargement de la page), garde
    l'historique de son activité, et relaie ses événements à la session
    vocale connectée."""

    def __init__(self):
        self.agent = None
        self.session: "VoiceSession | None" = None
        self.activity: list[dict] = []   # un élément par tour de l'agent
        self._pump: asyncio.Task | None = None
        self._perm_timers: dict[str, asyncio.Task] = {}

    async def start_agent(self, resume: bool = True) -> None:
        state = load_state() if resume else {}
        if state.get("agent") != AGENT_NAME:
            state = {}
        self.agent = create_agent(AGENT_NAME, AgentConfig(
            workdir=WORKDIR,
            system_prompt=(ROOT / "jarvis.md").read_text("utf-8"),
            model=AGENT_MODEL,
            resume=state.get("session_id"),
        ))
        self._pump = asyncio.create_task(self._pump_events(self.agent))
        try:
            await self.agent.start()
        except Exception as exc:  # agent pas installé... : la page l'affichera
            log.error("Impossible de démarrer l'agent : %s", exc)
            return
        log.info("Agent %s prêt (session %s)", self.agent.name,
                 state.get("session_id") or "nouvelle")

    async def reset(self) -> None:
        """Nouvelle conversation : l'agent oublie tout."""
        if self.agent:
            await self.agent.stop()
        if self._pump:
            self._pump.cancel()
        STATE_FILE.unlink(missing_ok=True)
        self.activity.clear()
        await self.start_agent(resume=False)
        await self.broadcast({"type": "reset"})

    async def broadcast(self, payload: dict) -> None:
        if self.session:
            await self.session.send_json(payload)

    async def _pump_events(self, agent) -> None:
        async for ev in agent.events():
            try:
                await self._on_event(ev)
            except Exception:
                log.exception("Erreur en traitant l'événement %s", ev.kind)

    async def _on_event(self, ev: AgentEvent) -> None:
        if ev.kind == "session":
            save_state({"agent": AGENT_NAME, "session_id": ev.data["session_id"]})
            return

        turn = self.activity[-1] if self.activity else None
        if ev.kind == "turn_start":
            turn = {"id": len(self.activity) + 1, "prompt": ev.text, "status": "running",
                    "steps": [], "reply": "", "started": time.time(), "ended": None}
            self.activity.append(turn)
            del self.activity[:-30]  # on garde les 30 derniers tours
        elif turn is None:
            return
        elif ev.kind == "text_delta":
            turn["reply"] += ev.text
        elif ev.kind == "tool_start":
            turn["steps"].append({"id": ev.tool_id, "tool": ev.tool_name,
                                  "label": ev.text, "status": "running"})
        elif ev.kind == "tool_end":
            for step in turn["steps"]:
                if step["id"] == ev.tool_id:
                    step["status"] = "error" if ev.data.get("is_error") else "done"
        elif ev.kind == "permission_request":
            self._perm_timers[ev.tool_id] = asyncio.create_task(
                self._permission_timeout(ev.tool_id))
        elif ev.kind == "turn_done":
            turn["status"] = "error" if ev.data.get("is_error") else "done"
            turn["ended"] = time.time()
            if not turn["reply"].strip():
                turn["reply"] = ev.text

        await self.broadcast({"type": "agent", "event": ev.kind, "text": ev.text,
                              "tool_id": ev.tool_id, "tool_name": ev.tool_name,
                              "data": {k: v for k, v in ev.data.items() if k != "input"},
                              "turn": turn, "busy": self.agent.busy})
        if self.session:
            await self.session.on_agent_event(ev)

    async def answer_permission(self, request_id: str, allow: bool) -> None:
        timer = self._perm_timers.pop(request_id, None)
        if timer:
            timer.cancel()
        await self.agent.respond_permission(request_id, allow)
        await self.broadcast({"type": "permission_answered", "id": request_id, "allow": allow})

    async def _permission_timeout(self, request_id: str) -> None:
        await asyncio.sleep(PERMISSION_TIMEOUT)
        self._perm_timers.pop(request_id, None)
        await self.agent.respond_permission(request_id, False)
        await self.broadcast({"type": "permission_answered", "id": request_id, "allow": False})


HUB = Hub()

# ------------------------------------------------------------------ session vocale

class VoiceSession:
    """Une connexion du navigateur : micro -> Gradium -> agent -> Gradium -> haut-parleur."""

    def __init__(self, ws: fastapi.WebSocket, voice_id: str):
        self.ws = ws
        self.client = gradium.client.GradiumClient(base_url=GRADIUM_BASE_URL,
                                                   api_key=GRADIUM_API_KEY)
        self.listener = Listener(self.client, "fr", self.on_user_text, self.on_user_turn,
                                 self.on_error, end_of_turn=END_OF_TURN, keywords=KEYWORDS)
        self.speaker = Speaker(self.client, voice_id, self.on_audio, self.on_error)
        self.playback_end = 0.0   # heure estimée de fin de lecture côté navigateur
        self.muted = False        # True : on ne lit plus la suite du tour en cours
        self.spoke_this_turn = False
        self._send_lock = asyncio.Lock()

    # -- envoi vers le navigateur
    async def send_json(self, payload: dict) -> None:
        with contextlib.suppress(Exception):
            async with self._send_lock:
                await self.ws.send_json(payload)

    async def on_audio(self, pcm: bytes) -> None:
        now = time.monotonic()
        self.playback_end = max(now, self.playback_end) + len(pcm) / 2 / SAMPLE_RATE
        with contextlib.suppress(Exception):
            async with self._send_lock:
                await self.ws.send_bytes(pcm)

    @property
    def speaking(self) -> bool:
        return time.monotonic() < self.playback_end

    async def on_error(self, message: str) -> None:
        await self.send_json({"type": "error", "message": message})

    async def shut_up(self) -> None:
        """Coupe la voix de Jarvis, tout de suite."""
        await self.speaker.cancel()
        self.playback_end = 0.0
        await self.send_json({"type": "clear_audio"})

    # -- ce que tu dis
    async def on_user_text(self, text: str) -> None:
        await self.send_json({"type": "transcript", "role": "user", "text": text})
        if BARGE_IN and self.speaking and normalize(text):
            log.debug("barge-in : %r", text)
            # Tu parles pendant que Jarvis parle : il se tait et n'enchaîne pas
            # sur la suite de sa réponse en cours.
            self.muted = True
            await self.shut_up()

    async def on_user_turn(self, phrase: str) -> None:
        await self.send_json({"type": "turn_end", "text": phrase})
        if not normalize(phrase):
            return  # que de la ponctuation (un « ? » transcrit en retard...)
        if normalize(phrase) in STOP_WORDS:
            self.muted = True
            await self.shut_up()
            await HUB.agent.interrupt()
            return
        try:
            await HUB.agent.send(phrase)
        except Exception as exc:
            log.error("Agent : %s", exc)
            await self.on_error(f"Agent : {exc}")

    # -- ce que fait l'agent
    async def on_agent_event(self, ev: AgentEvent) -> None:
        log.debug("agent %s %r (muted=%s)", ev.kind, ev.text[:60], self.muted)
        if ev.kind == "turn_start":
            self.muted, self.spoke_this_turn = False, False
        elif ev.kind == "text_delta" and not self.muted:
            self.spoke_this_turn = True
            self.speaker.say(ev.text)
        elif ev.kind == "text_break" and not self.muted:
            self.speaker.end()
        elif ev.kind == "permission_request":
            self.speaker.end()
            self.speaker.say("J'ai besoin de ton autorisation, regarde l'écran. ")
            self.speaker.end()
        elif ev.kind == "turn_done" and ev.data.get("is_error") and not self.muted:
            if not self.spoke_this_turn and ev.text != "Tâche interrompue.":
                self.speaker.say("Désolé, ça n'a pas marché. Le détail est à l'écran. ")
                self.speaker.end()

    # -- boucle principale
    async def run(self) -> None:
        self.listener.start()
        self.speaker.start()
        if GREETING:
            self.speaker.say(GREETING + " ")
            self.speaker.end()
        try:
            while True:
                msg = await self.ws.receive()
                if msg["type"] == "websocket.disconnect":
                    break
                if msg.get("bytes"):
                    self.listener.feed(msg["bytes"])
                elif msg.get("text"):
                    await self.on_command(json.loads(msg["text"]))
        finally:
            await self.listener.stop()
            await self.speaker.stop()

    async def on_command(self, cmd: dict) -> None:
        kind = cmd.get("type")
        if kind == "interrupt":            # bouton « Stop »
            self.muted = True
            await self.shut_up()
            await HUB.agent.interrupt()
        elif kind == "permission":         # boutons Autoriser / Refuser
            await HUB.answer_permission(cmd.get("id", ""), bool(cmd.get("allow")))
        elif kind == "reset":              # bouton « Nouvelle conversation »
            await self.shut_up()
            await HUB.reset()
        elif kind == "say":                # champ texte : écrire au lieu de parler
            text = str(cmd.get("text", "")).strip()
            if text:
                await self.on_user_turn(text)

# ------------------------------------------------------------------ serveur

@contextlib.asynccontextmanager
async def lifespan(_app):
    await HUB.start_agent()
    yield
    if HUB.agent:
        await HUB.agent.stop()


app = fastapi.FastAPI(title="JARVIS x Gradium", lifespan=lifespan)


def allowed_origin(origin: str | None) -> bool:
    # Le serveur peut lancer des actions sur ta machine : seule la page
    # servie par Jarvis lui-même a le droit de s'y connecter.
    return origin in {f"http://127.0.0.1:{PORT}", f"http://localhost:{PORT}"}


@app.websocket("/ws")
async def ws_endpoint(ws: fastapi.WebSocket):
    if not allowed_origin(ws.headers.get("origin")):
        await ws.close(code=4003)
        return
    await ws.accept()
    start = await ws.receive_json()
    if not GRADIUM_API_KEY:
        await ws.send_json({"type": "error", "message": "GRADIUM_API_KEY manquante dans .env"})
        await ws.close()
        return
    if HUB.session:  # une seule page à la fois : l'ancienne est déconnectée
        with contextlib.suppress(Exception):
            await HUB.session.ws.close(code=4000, reason="Ouvert dans un autre onglet")
    session = VoiceSession(ws, resolve_voice(start.get("voice_id") or VOICE))
    HUB.session = session
    await session.send_json({"type": "hello", "activity": HUB.activity,
                             "busy": HUB.agent.busy})
    try:
        await session.run()
    except fastapi.WebSocketDisconnect:
        pass
    finally:
        if HUB.session is session:
            HUB.session = None


@app.get("/api/info")
async def info():
    """Pour la page : ce qui est configuré, ce qui manque."""
    from agents.base import find_executable
    cli = {"claude": "claude", "codex": "codex", "gemini": "gemini"}.get(AGENT_NAME, AGENT_NAME)
    return {
        "agent": HUB.agent.name if HUB.agent else AGENT_NAME,
        "workdir": str(WORKDIR),
        "voices": [{"name": n, "voice_id": v} for n, v in VOICES.items()],
        "default_voice_id": resolve_voice(VOICE),
        "checks": {
            "gradium_key": bool(GRADIUM_API_KEY),
            "agent_cli": find_executable(cli) is not None or bool(os.environ.get("JARVIS_CLAUDE_BIN")),
            "agent_cli_name": cli,
        },
    }


app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")


@app.get("/")
async def index():
    return FileResponse(ROOT / "static" / "index.html")


if __name__ == "__main__":
    import threading
    import webbrowser

    import uvicorn

    url = f"http://127.0.0.1:{PORT}"
    print(f"\n  JARVIS x Gradium -> {url}   (Ctrl+C pour arrêter)\n")
    if os.environ.get("JARVIS_OPEN_BROWSER", "true").lower() == "true":
        threading.Timer(1.5, webbrowser.open, [url]).start()
    # 127.0.0.1 seulement : ne jamais exposer ce serveur sur le réseau.
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")
