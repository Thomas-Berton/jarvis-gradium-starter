"""La voix de Jarvis : écoute (STT) et parole (TTS) avec Gradium.

- Listener : envoie le micro à Gradium, reçoit le texte, et détecte la fin
  de ta phrase grâce au VAD sémantique de Gradium (probabilité que tu aies
  fini de parler, calculée toutes les 80 ms).
- Speaker : reçoit le texte de l'agent au fil de l'eau et le transforme en
  audio, en streaming. Peut être coupé net (quand tu interromps Jarvis).

Doc : https://docs.gradium.ai/guides/recipes/turn-taking
      https://docs.gradium.ai/guides/recipes/llm-to-tts
"""

import asyncio
import logging
import re
from typing import Awaitable, Callable

import gradium

log = logging.getLogger("jarvis.voice")

SAMPLE_RATE = 24000          # micro et haut-parleur : PCM 16 bits mono 24 kHz
CHUNK_BYTES = 1920 * 2       # Gradium travaille par trames de 80 ms


class Listener:
    """Écoute en continu. Appelle on_text à chaque bout de phrase reconnu,
    puis on_turn avec la phrase complète quand tu as fini de parler."""

    def __init__(
        self,
        client: gradium.client.GradiumClient,
        language: str,
        on_text: Callable[[str], Awaitable[None]],
        on_turn: Callable[[str], Awaitable[None]],
        on_error: Callable[[str], Awaitable[None]],
        end_of_turn: float = 0.8,
        keywords: list[str] | None = None,
    ):
        self.client = client
        self.language = language
        self.on_text, self.on_turn, self.on_error = on_text, on_turn, on_error
        # Seuil sur la probabilité « silence pendant les 2 prochaines secondes ».
        # Plus haut = Jarvis attend plus longtemps avant de répondre.
        self.end_of_turn = end_of_turn
        self.keywords = keywords or []
        self.audio: asyncio.Queue[bytes] = asyncio.Queue(maxsize=200)
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._run_forever())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)

    def feed(self, pcm: bytes) -> None:
        try:
            self.audio.put_nowait(pcm)
        except asyncio.QueueFull:
            pass  # réseau lent : on perd un bout d'audio plutôt que de bloquer

    async def _run_forever(self) -> None:
        # Une session STT Gradium dure au plus 50 minutes : on se reconnecte.
        while True:
            try:
                await self._session()
                await asyncio.sleep(0.5)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("STT : %s", exc)
                await self.on_error(f"Écoute Gradium : {exc}")
                await asyncio.sleep(3)

    async def _session(self) -> None:
        config = {"language": self.language, "delay_in_frames": 8}
        if self.keywords:
            config["keywords"] = {"words": self.keywords, "boost": 3}
        async with self.client.stt_realtime(
            model_name="default", input_format="pcm", json_config=config
        ) as stt:
            sender = asyncio.create_task(self._send_audio(stt))
            try:
                await self._receive(stt)
            finally:
                sender.cancel()

    async def _send_audio(self, stt) -> None:
        buf = b""
        while True:
            buf += await self.audio.get()
            while len(buf) >= CHUNK_BYTES:
                await stt.send_audio(buf[:CHUNK_BYTES])
                buf = buf[CHUNK_BYTES:]

    async def _receive(self, stt) -> None:
        words: list[str] = []
        quiet_steps, flush_id, flushing = 0, 0, False
        async for msg in stt:
            kind = msg.get("type")
            if kind == "text":
                text = msg.get("text", "").strip()
                if text:
                    words.append(text)
                    await self.on_text(text)
            elif kind in ("step", "vad"):
                vad = msg.get("vad") or []
                if not vad or not words or flushing:
                    quiet_steps = 0
                    continue
                prob = vad[min(2, len(vad) - 1)]["inactivity_prob"]
                quiet_steps = quiet_steps + 1 if prob > self.end_of_turn else 0
                if quiet_steps >= 2:
                    # Tu as fini de parler : on demande à Gradium de livrer
                    # tout de suite les derniers mots en attente.
                    flush_id += 1
                    flushing = True
                    await stt.send_flush(flush_id=flush_id)
            elif kind == "flushed":
                flushing, quiet_steps = False, 0
                phrase = " ".join(words).strip()
                words = []
                if phrase:
                    await self.on_turn(phrase)
            elif kind == "end_of_stream":
                return


# ------------------------------------------------------------------ parole

_CODE_FENCE = re.compile(r"```")
_MARKDOWN = re.compile(r"(\*\*|__|\*|`|^#+\s*|^\s*[-•]\s+)", re.MULTILINE)
_LINK = re.compile(r"\[([^\]]+)\]\([^)]+\)")


class Speaker:
    """Transforme le texte de l'agent en voix, au fil de l'eau."""

    def __init__(
        self,
        client: gradium.client.GradiumClient,
        voice_id: str,
        on_audio: Callable[[bytes], Awaitable[None]],
        on_error: Callable[[str], Awaitable[None]],
    ):
        self.client = client
        self.voice_id = voice_id
        self.on_audio, self.on_error = on_audio, on_error
        self._queue: asyncio.Queue[str | None] = asyncio.Queue()
        self._buffer = ""
        self._in_code = False
        self._worker: asyncio.Task | None = None

    def start(self) -> None:
        self._worker = asyncio.create_task(self._work())

    async def stop(self) -> None:
        if self._worker:
            self._worker.cancel()
            await asyncio.gather(self._worker, return_exceptions=True)

    def say(self, text: str) -> None:
        """Ajoute du texte (même un morceau de mot). Seuls les mots complets partent."""
        self._buffer += text
        cut = max(self._buffer.rfind(" "), self._buffer.rfind("\n"))
        if cut >= 0:
            ready, self._buffer = self._buffer[:cut], self._buffer[cut + 1:]
            self._push(ready)

    def end(self) -> None:
        """Fin d'un bloc de texte : on prononce ce qui reste."""
        self._push(self._buffer)
        self._buffer = ""
        self._queue.put_nowait(None)

    async def cancel(self) -> None:
        """Coupe la parole immédiatement (tu as interrompu Jarvis)."""
        self._buffer, self._in_code = "", False
        while not self._queue.empty():
            self._queue.get_nowait()
        await self.stop()
        self.start()

    def _push(self, text: str) -> None:
        # Le code et le markdown ne se lisent pas à voix haute.
        parts = _CODE_FENCE.split(text)
        spoken = []
        for i, part in enumerate(parts):
            if i > 0:
                self._in_code = not self._in_code
            if not self._in_code:
                spoken.append(part)
        text = _MARKDOWN.sub("", _LINK.sub(r"\1", "".join(spoken)))
        text = " ".join(text.split())
        if text:
            self._queue.put_nowait(text)

    async def _work(self) -> None:
        while True:
            first = await self._queue.get()
            if first is None:
                continue
            try:
                await self._speak(first)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("TTS : %s", exc)
                await self.on_error(f"Voix Gradium : {exc}")

    async def _speak(self, first: str) -> None:
        """Une session TTS par bloc de texte : ouverte au premier mot,
        fermée quand le bloc est fini."""
        log.debug("TTS : %r", first)
        async with self.client.tts_realtime(
            model_name="default",
            voice_id=self.voice_id,
            output_format="pcm_24000",
            json_config={"rewrite_rules": "fr"},
        ) as tts:

            async def pump_audio():
                async for msg in tts:
                    if msg.get("type") == "audio":
                        await self.on_audio(msg["audio"])
                    elif msg.get("type") == "end_of_stream":
                        return

            reader = asyncio.create_task(pump_audio())
            try:
                text = first
                while text is not None:
                    await tts.send_text(text)
                    text = await self._queue.get()
                await tts.send_text("<flush>")
                await tts.send_eos()
                await reader
            finally:
                reader.cancel()
