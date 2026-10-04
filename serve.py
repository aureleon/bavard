#!/usr/bin/env python3
"""
Bavard sidecar for the native app: the tutor.py engine behind a JSON-lines
protocol.

  stdin   one JSON command per line   {"cmd": "ptt_start"}
  stdout  one JSON event per line     {"event": "state", "state": "listening", ...}
  stderr  logs (never parsed)

Commands
  ptt_start / ptt_stop           push-to-talk (ptt_stop is automatic at 30 s)
  stop                           stop the tutor's voice now
  set_speed {speed}              0.5 - 1.6 (UI uses 0.75 - 1.25)
  set_turn_mode {mode}           ptt | vad | semantic (semantic loads Kyutai STT)
  pause / resume                 hands-free modes: close / reopen the mic
  replay {id, speed?}            speak a tutor reply again (id 0 = greeting)
  translate {id}                 English for one turn (cache-less, cached per turn)
  vocab {id}                     A2/B1 expressions in a tutor reply (cache-less)
  stats                          context, cache and memory
  reload_prompts                 re-read the prompt files (keeps the conversation)
  translate_prompt {name, text, req?}   English prompt -> French (streamed)
  shutdown                       exit now
  Debug: text_turn {text}, file_turn {path}

Events
  hello, loading {stage}, download {repo, done, total}, warning {message},
  ready {config}, state {state, mic}, level {src, rms, bands},
  partial {text}, notice {key}, user_turn {id, transcript, pronunciation},
  reply_delta {id, correction, reponse}, reply_done {id, correction, reponse},
  turn_stats {id, ...}, translation {id, ...}, vocab {id, items}, stats {...},
  speed {speed}, mode {mode}, prompts_reloaded {seconds},
  prompt_translation {name, req, text, done, missing?},
  error {message}, fatal {message}, bye

All MLX work (Gemma, Whisper) runs on the main thread, because MLX binds
arrays to per-thread streams. Kyutai models run on their own threads (see
tutor.py). Side requests (translate, vocab) never use the tutor prefix cache.
"""

import os
import sys

# The protocol owns the real stdout. Point fd 1 at stderr so prints from this
# program or any library can never corrupt the protocol stream.
_PROTO = os.fdopen(os.dup(1), "w", buffering=1, encoding="utf-8")
os.dup2(2, 1)
sys.stdout = sys.stderr

import argparse
import itertools
import json
import queue
import re
import threading
import time
import traceback

# ---------------------------------------------------------------------------
# Event channel
# ---------------------------------------------------------------------------

_out_q = queue.Queue()


def emit(event, **data):
    """Queue an event. Safe from any thread, including audio callbacks."""
    _out_q.put({"event": event, **data})


def _writer():
    while True:
        msg = _out_q.get()
        if msg is None:
            _out_q.task_done()
            return
        try:
            _PROTO.write(json.dumps(msg, ensure_ascii=False) + "\n")
            _PROTO.flush()
        except (BrokenPipeError, ValueError):
            os._exit(0)  # the app is gone
        finally:
            _out_q.task_done()


threading.Thread(target=_writer, daemon=True).start()


def shutdown(code=0):
    emit("bye")
    _out_q.put(None)
    _out_q.join()
    os._exit(code)


emit("hello", pid=os.getpid())
emit("loading", stage="imports")

import numpy as np  # noqa: E402

import tutor as T  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

LEVEL_HZ = 30
BANDS = 8
SPEAKER_NAMES = re.compile(r"speaker|macbook|imac|display|studio|built-in|haut-parleur", re.I)


class LevelMeter:
    """Turns audio blocks into ~30 Hz `level` events for the orb."""

    def __init__(self, src, mute=False):
        self.src = src
        self.mute = mute
        self.last = 0.0

    def __call__(self, pcm):
        if self.mute:
            pcm[:] = 0  # test mode: the output block is written in place
        now = time.monotonic()
        if now - self.last < 1.0 / LEVEL_HZ or not len(pcm):
            return
        self.last = now
        x = np.asarray(pcm, dtype=np.float32)
        rms = float(np.sqrt(np.mean(x * x)))
        spec = np.abs(np.fft.rfft(x * np.hanning(len(x))))
        edges = np.unique(np.geomspace(1, len(spec), BANDS + 1).astype(int))
        ref = len(x) / 4
        bands = []
        for a, b in zip(edges[:-1], edges[1:]):
            v = spec[a:b].mean() if b > a else 0.0
            bands.append(round(float(np.clip((20 * np.log10(v / ref + 1e-9) + 70) / 70, 0, 1)), 3))
        level = float(np.clip((20 * np.log10(rms + 1e-9) + 60) / 60, 0, 1))
        emit("level", src=self.src, rms=round(level, 3), bands=bands)


def memory_estimate_gb(stt, turn, tts):
    """Peak memory measured on an M4 Pro (README, "Memory and speed")."""
    kyutai_stt = stt == "kyutai" or turn == "semantic"
    if tts == "kyutai":
        return 11.2 if kyutai_stt else 10.0
    if kyutai_stt:
        return 7.2
    return 6.1 if stt == "whisper" else 5.9


def memory_budget():
    """What macOS says the GPU may use: Metal's recommendedMaxWorkingSetSize
    (about 2/3 to 3/4 of unified memory, depending on the Mac)."""
    import mlx.core as mx

    info_fn = getattr(mx, "device_info", None) or mx.metal.device_info
    try:
        info = info_fn()
    except Exception:
        return {"device": None, "total_gb": None, "recommended_gb": None}
    gb = lambda n: round(n / 1e9, 1) if n else None
    return {
        "device": info.get("device_name"),
        "total_gb": gb(info.get("memory_size")),
        "recommended_gb": gb(info.get("max_recommended_working_set_size")),
    }


def check_budget(stt, turn, tts):
    """Warn when a setup is expected to go past the recommended working set."""
    budget = memory_budget()
    need = memory_estimate_gb(stt, turn, tts)
    limit = budget["recommended_gb"]
    if limit and need > limit:
        emit("warning", key="memory", need_gb=need, budget_gb=limit,
             message=f"This setup needs ~{need} GB, more than the {limit} GB that macOS "
                     f"recommends for the GPU on this Mac ({budget['total_gb']} GB). "
                     "Expect swapping and slow replies.")
    return budget, need


def is_ras(text):
    return not text or text.strip().upper().rstrip(".!") in ("RAS", "AUCUN", "AUCUNE", "NONE", "")


def partial_reply(text):
    """Split a reply that is still streaming -> (correction, reponse)."""
    m = T.REPLY_MARKER.search(text)
    if not m:
        return T.CORRECTION_MARKER.sub("", text).strip(), ""
    return T.CORRECTION_MARKER.sub("", text[: m.start()]).strip(), text[m.end():].strip()


def read_prompt(path, kind):
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read().strip()
    except OSError as e:
        raise RuntimeError(f"Cannot read the {kind} prompt ({path}): {e}")
    if not text:
        raise RuntimeError(f"The {kind} prompt is empty: {path}")
    missing = [m for m in T.REQUIRED_MARKERS[kind] if m.lower() not in text.lower()]
    if missing:
        emit("warning", key="prompt_marker", path=path, markers=missing,
             message=f"{os.path.basename(path)} does not mention {', '.join(missing)}: "
                     "the output format may no longer be recognized.")
    return text


# ---------------------------------------------------------------------------
# Model download with byte progress
# ---------------------------------------------------------------------------

def _dir_bytes(path):
    total = 0
    for root, _, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def ensure_downloaded(repo, allow_patterns=None):
    """Download `repo` (if needed) and emit `download` events with byte counts."""
    from huggingface_hub import HfApi, snapshot_download
    from huggingface_hub.constants import HF_HUB_CACHE

    try:
        snapshot_download(repo, allow_patterns=allow_patterns, local_files_only=True)
        return  # already cached
    except Exception:
        pass

    import fnmatch
    total = None
    try:
        info = HfApi().model_info(repo, files_metadata=True)
        files = [s for s in info.siblings
                 if not allow_patterns or any(fnmatch.fnmatch(s.rfilename, p) for p in allow_patterns)]
        total = sum(s.size or 0 for s in files) or None
    except Exception:
        pass

    blobs = os.path.join(HF_HUB_CACHE, "models--" + repo.replace("/", "--"), "blobs")
    start = _dir_bytes(blobs)
    done = threading.Event()

    def poll():
        while not done.wait(0.5):
            emit("download", repo=repo, done=_dir_bytes(blobs) - start, total=total)

    threading.Thread(target=poll, daemon=True).start()
    emit("download", repo=repo, done=0, total=total)
    try:
        snapshot_download(repo, allow_patterns=allow_patterns)
    finally:
        done.set()
    emit("download", repo=repo, done=total or _dir_bytes(blobs) - start, total=total, finished=True)


# ---------------------------------------------------------------------------
# Push-to-talk capture
# ---------------------------------------------------------------------------

class PttRecorder:
    """Records between ptt_start and ptt_stop. With a Kyutai listener, audio is
    streamed to it at 24 kHz (live partial transcript)."""

    def __init__(self, listener, on_level, on_limit):
        self.listener = listener
        self.rate = T.KYUTAI_SAMPLE_RATE if listener else T.SAMPLE_RATE
        self.on_level = on_level
        self.on_limit = on_limit
        self.stream = None
        self.chunks = []
        self.samples = 0
        self.limit_hit = False
        self.lock = threading.Lock()

    @property
    def active(self):
        return self.stream is not None

    def _callback(self, indata, frames, time_info, status):
        with self.lock:
            if self.stream is None:
                return
            self.chunks.append(indata[:, 0].copy())
            self.samples += frames
        if self.listener:
            self.listener.feed(indata[:, 0])
        self.on_level(indata[:, 0])
        if not self.limit_hit and self.samples >= T.MAX_AUDIO_SECONDS * self.rate:
            self.limit_hit = True
            self.on_limit()

    def start(self):
        import sounddevice as sd

        if self.stream is not None:
            return
        self.chunks, self.samples, self.limit_hit = [], 0, False
        if self.listener:
            self.listener.begin()
        stream = sd.InputStream(samplerate=self.rate, channels=1, dtype="float32",
                                blocksize=T.KyutaiListener.BLOCK if self.listener else 1024,
                                callback=self._callback)
        with self.lock:
            self.stream = stream
        stream.start()

    def stop(self):
        """-> 16 kHz recording, or None if not recording."""
        with self.lock:
            stream, self.stream = self.stream, None
        if stream is None:
            return None
        stream.stop()
        stream.close()
        if not self.chunks:
            return np.zeros(0, dtype=np.float32)
        return T.to_16k(np.concatenate(self.chunks), self.rate)


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

PRIO_TURN, PRIO_USER, PRIO_BACKGROUND = 0, 1, 2

TRANSLATE_SYSTEM = (
    "You translate French into natural, simple English for a beginner learner. "
    "Translate every numbered line. Keep the numbers. Output only the numbered lines, nothing else."
)
VOCAB_SYSTEM = (
    "Tu aides un élève débutant (A1/A2) à apprendre le français. Dans le texte du tuteur, "
    "relève au plus 3 expressions ou mots utiles de niveau A2/B1 (expressions idiomatiques, "
    "tournures courantes). Ignore les mots très simples (je, bonjour, est, et, marché...).\n"
    "Format : une ligne par expression, trois champs séparés par \"|\" :\n"
    "extrait copié mot pour mot du texte | forme du dictionnaire | traduction anglaise\n"
    "Exemple : \"pris ton temps | prendre son temps | to take one's time\"\n"
    "S'il n'y en a aucune, écris RIEN."
)


PROMPT_TRANSLATE_SYSTEM = (
    "You translate system prompts for a French tutoring app from English into French.\n"
    "Rules:\n"
    "- Translate everything into natural, clear French. Address the model with \"tu\".\n"
    "- The French prompt must ask for the output format with these markers, written EXACTLY "
    "like this (uppercase, accents, colon): {markers}. Never translate or change them.\n"
    "- Keep the structure: line breaks, numbered rules, bullets, quotes and placeholders like <...>.\n"
    "- Keep French example sentences exactly as they are, including their mistakes: they are "
    "examples of learner errors.\n"
    "- Output only the translated prompt. No introduction, no comment, no markdown fence."
)


class Engine:
    def __init__(self, args):
        self.args = args
        self.jobs = queue.PriorityQueue()
        self.seq = itertools.count()
        self.turns = {}
        self.next_id = 1
        self.state = None
        self.mode = args.turn
        self.paused = False
        self.idle = threading.Event()
        self.hf_cancel = threading.Event()  # stops the current hands-free recording
        self.hf_wake = threading.Event()    # mode / pause changed
        self.interrupted = False            # the current reply was stopped by the learner
        self.listener = None
        self.vad = None
        self.vad_key = None
        self.ptt = None
        self.ptt_t_end = None
        self.mic_meter = LevelMeter("mic")
        self.out_meter = LevelMeter("out", mute=args.mute)

    # -- state -------------------------------------------------------------
    def set_state(self, state, **extra):
        if state == self.state and not extra and state != "idle":
            return
        self.state = state
        if state == "idle":
            self.idle.set()
        else:
            self.idle.clear()
        mic = state == "listening" or (state == "idle" and self.mode != "ptt" and not self.paused)
        emit("state", state=state, mic=mic, **extra)

    def settle(self):
        """Back to idle after a turn or a replay, unless the learner already
        barged in and started a new recording."""
        if self.state != "listening":
            self.set_state("idle")

    def submit(self, prio, fn, *a, **kw):
        self.jobs.put((prio, next(self.seq), fn, a, kw))

    # -- loading -----------------------------------------------------------
    def load(self):
        a = self.args
        self.set_state("loading")
        model_repo = T.MODEL_ALIASES.get(a.model.lower(), a.model)
        self.model_repo = model_repo
        voice_default = "ff_siwis" if a.tts == "kokoro" else "cml-tts/fr/10087_11650_000028-0002.wav"
        self.voice = voice_default if a.voice == "default" else a.voice

        check_budget(a.stt, a.turn, a.tts)
        emit("loading", stage="download")
        ensure_downloaded(model_repo)
        if a.tts == "kokoro":
            ensure_downloaded("hexgrad/Kokoro-82M",
                              ["config.json", "kokoro-v1_0.pth", f"voices/{self.voice}.pt"])
        else:
            ensure_downloaded("kyutai/tts-1.6b-en_fr")
        if a.stt == "whisper":
            ensure_downloaded(a.whisper_model)
        if a.stt == "kyutai" or a.turn == "semantic":
            ensure_downloaded(T.KyutaiListener.REPO)

        tutor_prompt = read_prompt(a.tutor_prompt, "tutor")
        hear_prompt = read_prompt(a.hear_prompt, "hear") if a.stt == "audio" else ""
        T.quiet_hugging_face()

        emit("loading", stage="llm")
        self.tutor = T.Tutor(model_repo, a.max_context, a.temperature, tutor_prompt, hear_prompt)
        if a.stt == "audio" and not self.tutor.supports_audio:
            raise RuntimeError(f"{model_repo} does not accept audio. Use stt=whisper or stt=kyutai.")

        if a.stt == "whisper":
            emit("loading", stage="whisper")
            import mlx_whisper
            self.whisper = mlx_whisper
            mlx_whisper.transcribe(np.zeros(T.SAMPLE_RATE, dtype=np.float32),
                                   path_or_hf_repo=a.whisper_model, language="fr")

        emit("loading", stage="tts")
        if a.tts == "kyutai":
            make = lambda: T.KyutaiSpeaker(voice=self.voice, quantize_bits=a.kyutai_bits)
            prebuffer = 0.24
        else:
            make = lambda: T.KokoroSpeaker(voice=self.voice, speed=a.speed)
            prebuffer = 0.0
        self.speech = T.SpeechQueue(make, prebuffer_s=prebuffer)
        self.speech.speaker.set_speed(a.speed)
        self.speech.player.on_level = self.out_meter

        if a.stt == "kyutai" or a.turn == "semantic":
            self._load_listener()
        self.ptt = PttRecorder(self.listener, self.mic_meter, self._ptt_limit)
        if a.turn != "ptt":
            self._ensure_vad()

        emit("loading", stage="warmup")
        self.tutor.seed_greeting(T.GREETING)
        self.tutor.warm_up(audio=a.stt == "audio")

    def _load_listener(self):
        if self.listener is None:
            emit("loading", stage="kyutai_stt")
            self.listener = T.KyutaiListener(quantize_bits=self.args.kyutai_stt_bits,
                                             eot_threshold=self.args.eot_threshold,
                                             on_partial=lambda text: emit("partial", text=text))
            if self.ptt is not None and not self.ptt.active:
                self.ptt = PttRecorder(self.listener, self.mic_meter, self._ptt_limit)

    def _ensure_vad(self):
        key = (self.mode, self.args.silence, self.listener is not None)
        if self.vad is None or self.vad_key != key:
            self.vad = T.VadRecorder(silence_s=self.args.silence, listener=self.listener,
                                     semantic=self.mode == "semantic")
            self.vad_key = key

    def config(self):
        import sounddevice as sd

        try:
            out_name = sd.query_devices(kind="output")["name"]
        except Exception:
            out_name = ""
        a = self.args
        budget = memory_budget()
        return {
            "memory": {**budget, "estimate_gb": memory_estimate_gb(a.stt, self.mode, a.tts)},
            "model": self.model_repo, "stt": a.stt, "turn": self.mode, "tts": a.tts,
            "voice": self.voice, "speed": self.speech.speaker.speed,
            "max_context": a.max_context, "max_audio_s": T.MAX_AUDIO_SECONDS,
            "output_device": out_name,
            "headphones_likely": bool(out_name) and not SPEAKER_NAMES.search(out_name),
            "prompts": {"tutor": a.tutor_prompt, "hear": a.hear_prompt},
        }

    # -- main loop ---------------------------------------------------------
    def run(self):
        try:
            self.load()
        except BaseException as e:  # includes SystemExit from tutor.py
            traceback.print_exc()
            emit("fatal", message=str(e) or e.__class__.__name__)
            shutdown(1)

        self.turns[0] = {"id": 0, "transcript": None, "pronunciation": None,
                         "correction": None, "reponse": T.GREETING}
        emit("ready", config=self.config())
        threading.Thread(target=self._stdin_loop, daemon=True).start()
        threading.Thread(target=self._hands_free_loop, daemon=True).start()

        emit("reply_done", id=0, correction=None, reponse=T.GREETING, greeting=True)
        if self.args.greet:
            self.set_state("speaking")
            self.speech.begin_turn(None)
            self.speech.say(T.GREETING)
            self.speech.wait()
        self.set_state("idle")
        self._prefetch(0)

        while True:
            _, _, fn, a, kw = self.jobs.get()
            try:
                fn(*a, **kw)
            except Exception as e:
                traceback.print_exc()
                emit("error", message=str(e))
                if self.state not in ("idle", "listening"):
                    self.set_state("idle")

    # -- commands (stdin thread) --------------------------------------------
    def _stdin_loop(self):
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
                self.command(msg)
            except Exception as e:
                traceback.print_exc()
                emit("error", message=f"bad command {line!r}: {e}")
        # stdin closed: the app is gone. Finish queued work first (lets
        # `echo '{...}' | serve.py` run one command), then exit.
        self.submit(PRIO_BACKGROUND + 1, shutdown, 0)

    def command(self, msg):
        cmd = msg.get("cmd")
        if cmd == "ptt_start":
            self.ptt_start()
        elif cmd == "ptt_stop":
            self.ptt_stop()
        elif cmd == "stop":
            self.stop_speaking()
        elif cmd == "set_speed":
            self.speech.speaker.set_speed(float(msg["speed"]))
            emit("speed", speed=self.speech.speaker.speed)
        elif cmd == "set_turn_mode":
            self.submit(PRIO_USER, self.set_turn_mode, msg["mode"])
        elif cmd in ("pause", "resume"):
            self.paused = cmd == "pause"
            self.hf_cancel.set()
            self.hf_wake.set()
            if self.state == "idle":
                self.set_state("idle")  # refresh the mic flag
        elif cmd == "replay":
            self.submit(PRIO_USER, self.replay, int(msg.get("id", 0)), msg.get("speed"))
        elif cmd == "translate":
            self.submit(PRIO_USER, self.translate, int(msg["id"]))
        elif cmd == "vocab":
            self.submit(PRIO_USER, self.vocab, int(msg["id"]))
        elif cmd == "stats":
            self.submit(PRIO_USER, self.stats)
        elif cmd == "translate_prompt":
            self.submit(PRIO_USER, self.translate_prompt, msg["name"], msg["text"], msg.get("req"))
        elif cmd == "reload_prompts":
            self.submit(PRIO_USER, self.reload_prompts)
        elif cmd == "text_turn":
            self.submit(PRIO_TURN, self.run_turn, None, time.time(), text=msg["text"])
        elif cmd == "file_turn":
            self.submit(PRIO_TURN, self.file_turn, msg["path"])
        elif cmd == "shutdown":
            shutdown(0)
        else:
            emit("error", message=f"unknown command: {cmd}")

    def ptt_start(self):
        if self.mode != "ptt" or self.ptt is None:
            return
        if self.state in ("thinking", "speaking"):
            self.stop_speaking()  # barge in
        elif self.state not in ("idle",):
            return  # still hearing / thinking: ignore
        self.ptt.start()
        self.set_state("listening", max_s=T.MAX_AUDIO_SECONDS)

    def _ptt_limit(self):
        emit("notice", key="limit", seconds=T.MAX_AUDIO_SECONDS)
        threading.Thread(target=self.ptt_stop, daemon=True).start()

    def ptt_stop(self):
        if self.ptt is None or not self.ptt.active:
            return
        t_end = time.time()
        audio = self.ptt.stop()
        self.set_state("hearing")
        self.submit(PRIO_TURN, self.run_turn, audio, t_end)

    def stop_speaking(self):
        if self.state in ("thinking", "speaking"):
            self.interrupted = True
        self.speech.cancel()

    # -- hands-free capture (own thread) ----------------------------------
    def _hands_free_loop(self):
        while True:
            if self.mode == "ptt" or self.paused or self.vad is None:
                self.hf_wake.wait()
                self.hf_wake.clear()
                continue
            self.idle.wait()
            self.hf_cancel.clear()
            if self.mode == "ptt" or self.paused:
                continue
            vad = self.vad
            try:
                audio = vad.record(cancel=self.hf_cancel, on_level=self.mic_meter, quiet=True,
                                   on_speech=lambda: self.set_state("listening"))
            except Exception as e:
                traceback.print_exc()
                emit("error", message=f"microphone: {e}")
                time.sleep(1)
                continue
            if audio is None:
                if self.state == "listening":
                    if self.listener:
                        self.listener.finish(flush=False)
                    self.set_state("idle")
                continue
            self.set_state("hearing")
            self.submit(PRIO_TURN, self.run_turn, audio, time.time())
            time.sleep(0.05)  # let the engine pick it up before waiting for idle

    def set_turn_mode(self, mode):
        if mode not in ("ptt", "vad", "semantic"):
            raise ValueError(f"unknown turn mode {mode}")
        if self.ptt is not None and self.ptt.active:
            self.ptt.stop()
        if mode == "semantic" and self.listener is None:
            check_budget(self.args.stt, mode, self.args.tts)
            self._load_listener()
        self.hf_cancel.set()
        self.mode = mode
        if mode != "ptt":
            emit("loading", stage="vad")
            self._ensure_vad()
        emit("mode", mode=mode)
        self.hf_wake.set()
        self.set_state("idle")

    # -- one turn (engine thread) ------------------------------------------
    def file_turn(self, path):
        import soundfile as sf

        audio, rate = sf.read(path, dtype="float32", always_2d=True)
        self.set_state("hearing")
        self.run_turn(T.to_16k(audio[:, 0], rate), time.time())

    def run_turn(self, audio, t_end, text=None):
        a, tutor = self.args, self.tutor
        pron = None
        if text is None:
            duration = len(audio) / T.SAMPLE_RATE
            if duration < 0.5 or not len(audio) or np.max(np.abs(audio)) < 0.01:
                if self.listener:
                    self.listener.finish(flush=False)
                emit("notice", key="too_short")
                self.set_state("idle")
                return
            if duration > T.MAX_AUDIO_SECONDS:
                audio = audio[: T.SAMPLE_RATE * T.MAX_AUDIO_SECONDS]

            # ---- hear --------------------------------------------------
            self.set_state("hearing")
            if self.listener and a.stt != "kyutai":
                self.listener.finish(flush=False)
            if a.stt == "audio":
                text, pron = tutor.hear(audio)
            elif a.stt == "whisper":
                text = self.whisper.transcribe(audio, path_or_hf_repo=a.whisper_model,
                                               language="fr").get("text", "").strip()
            else:
                text = self.listener.finish()
        t_heard = time.time()

        if not text or "(inaudible)" in text.lower():
            emit("notice", key="inaudible")
            self.set_state("idle")
            return

        tid = self.next_id
        self.next_id += 1
        turn = {"id": tid, "transcript": text, "pronunciation": pron, "correction": None, "reponse": ""}
        self.turns[tid] = turn
        emit("user_turn", id=tid, transcript=text, pronunciation=pron)

        user_content = f"TRANSCRIPTION: {text}"
        if pron:
            user_content += f"\nPRONONCIATION: {pron}"

        # ---- tutor + streaming speech ----------------------------------
        self.set_state("thinking")
        self.interrupted = False
        speech = self.speech
        speech.begin_turn(t_end)
        mark = speech.player.on_first_sound

        def first_sound():
            mark()
            self.set_state("speaking", id=tid)

        speech.player.on_first_sound = first_sound

        def say(chunk):
            if not self.interrupted:
                speech.say(chunk)

        chunker = T.Chunker(say)
        buf = {"text": "", "spoken": None}

        def on_text(delta):
            buf["text"] += delta
            corr, rep = partial_reply(buf["text"])
            if "RAS".startswith(corr.upper().rstrip(".")):
                corr = ""  # "R", "RA", "RAS": nothing to correct
            emit("reply_delta", id=tid, correction=corr, reponse=rep)
            if buf["spoken"] is None:
                m = T.REPLY_MARKER.search(buf["text"])
                if not m:
                    return
                buf["spoken"] = m.end()
            new = buf["text"][buf["spoken"]:]
            buf["spoken"] = len(buf["text"])
            if new:
                chunker.feed(new)

        raw = tutor.reply(user_content, on_text)
        if buf["spoken"] is None:
            _, rest = T.split_reply(buf["text"])
            chunker.feed(rest)
        chunker.flush()

        correction, spoken = T.split_reply(raw)
        correction = None if is_ras(correction) else correction
        turn.update(correction=correction, reponse=spoken)
        emit("reply_done", id=tid, correction=correction, reponse=spoken)

        speech.wait()
        s = tutor.last_stats or {}
        emit("turn_stats", id=tid,
             hear_s=round(t_heard - t_end, 3),
             ttft_s=round(s.get("ttft", 0.0), 3),
             first_audio_s=round(speech.first_audio_at, 3) if speech.first_audio_at else None,
             tok_s=round(s.get("tg_tps", 0.0), 1),
             peak_gb=round(s.get("peak_gb", 0.0), 2),
             cached_tokens=s.get("cached_tokens"), prompt_tokens=s.get("prompt_tokens"),
             interrupted=self.interrupted)
        self.settle()
        self._prefetch(tid)

    # -- side requests (engine thread, cache-less) -------------------------
    def _prefetch(self, tid):
        if self.args.prefetch:
            self.submit(PRIO_BACKGROUND, self.translate, tid)
            self.submit(PRIO_BACKGROUND, self.vocab, tid)

    def translate(self, tid):
        turn = self.turns.get(tid)
        if turn is None:
            return emit("error", message=f"no turn {tid}")
        if "translation" not in turn:
            fields = [k for k in ("transcript", "reponse") if turn.get(k)]
            lines = "\n".join(f"{i + 1}: {turn[k]}" for i, k in enumerate(fields))
            out = self.tutor.oneshot(TRANSLATE_SYSTEM, lines, max_tokens=300)
            got = dict(re.findall(r"^\s*(\d+)\s*[:.)]\s*(.*)$", out, re.M))
            turn["translation"] = {k: got.get(str(i + 1), "").strip() or None for i, k in enumerate(fields)}
        emit("translation", id=tid, **turn["translation"])

    def vocab(self, tid):
        turn = self.turns.get(tid)
        if turn is None or not turn.get("reponse"):
            return
        if "vocab" not in turn:
            text = turn["reponse"]
            out = self.tutor.oneshot(VOCAB_SYSTEM, text, max_tokens=120)
            items, seen = [], set()
            for line in out.splitlines():
                if "|" not in line:
                    continue
                parts = [p.strip(" -*\"'«»`") for p in line.split("|")]
                if len(parts) == 2:
                    parts = [parts[0], parts[0], parts[1]]
                if len(parts) < 3:
                    continue
                fr, lemma, en = parts[:3]
                key = fr.lower()
                # Keep only expressions that really occur in the reply, so the
                # UI can underline them.
                if fr and key not in seen and key in text.lower() and len(fr) > 2:
                    seen.add(key)
                    items.append({"fr": fr, "lemma": lemma or fr, "en": en})
            turn["vocab"] = items[:3]
        emit("vocab", id=tid, items=turn["vocab"])

    def replay(self, tid, speed=None):
        turn = self.turns.get(tid)
        if turn is None or not turn.get("reponse") or self.state != "idle":
            return
        speaker = self.speech.speaker
        old = speaker.speed
        if speed:
            speaker.set_speed(float(speed))
        self.interrupted = False
        self.set_state("speaking", id=tid, replay=True)
        try:
            self.speech.begin_turn(None)
            for chunk in re.split(r"(?<=[.!?…])\s+", turn["reponse"]):
                self.speech.say(chunk)
            self.speech.wait()
        finally:
            speaker.set_speed(old)
            self.settle()

    def translate_prompt(self, name, text, req=None):
        """English prompt -> French prompt, streamed as prompt_translation
        events. Cache-less, like translate / vocab."""
        if name not in T.REQUIRED_MARKERS:
            raise ValueError(f"unknown prompt {name}")
        markers = T.REQUIRED_MARKERS[name]
        system = PROMPT_TRANSLATE_SYSTEM.format(markers=", ".join(markers))
        tokens = len(self.tutor.tokenizer.encode(text))
        last = [0.0]

        def on_text(out):
            now = time.monotonic()
            if now - last[0] > 0.15:
                last[0] = now
                emit("prompt_translation", name=name, req=req, text=out, done=False)

        out = self.tutor.oneshot(system, text, max_tokens=max(400, int(tokens * 2.5)), on_text=on_text)
        out = re.sub(r"^\s*```[a-z]*\s*\n|\n\s*```\s*$", "", out.strip()).strip()
        missing = [m for m in markers if m.lower() not in out.lower()]
        emit("prompt_translation", name=name, req=req, text=out, done=True, missing=missing)

    def reload_prompts(self):
        """Re-read the prompt files and re-warm the prefix cache. The chat
        history is kept; only the system prompt changes."""
        a, tutor = self.args, self.tutor
        tutor_prompt = read_prompt(a.tutor_prompt, "tutor")
        hear_prompt = read_prompt(a.hear_prompt, "hear") if a.stt == "audio" else tutor.hear_prompt
        tutor.tutor_prompt, tutor.hear_prompt = tutor_prompt, hear_prompt
        # The cached tokens start with the old system prompt: start over.
        tutor.cache = T.PromptCacheState()
        t0 = time.time()
        tutor.warm_up(audio=False)
        emit("prompts_reloaded", seconds=round(time.time() - t0, 2))

    def stats(self):
        tutor = self.tutor
        emit("stats", context_tokens=tutor.context_tokens(), max_context=tutor.max_context,
             turns=sum(1 for m in tutor.history if m["role"] == "user") - 1,
             last=tutor.last_stats, speed=self.speech.speaker.speed)


def main():
    p = argparse.ArgumentParser(description="Bavard sidecar (JSON lines on stdin/stdout)")
    p.add_argument("--stt", choices=["audio", "whisper", "kyutai"], default="audio")
    p.add_argument("--turn", choices=["ptt", "vad", "semantic"], default="ptt")
    p.add_argument("--tts", choices=["kokoro", "kyutai"], default="kokoro")
    p.add_argument("--model", default="gemma-e4b")
    p.add_argument("--voice", default="default")
    p.add_argument("--speed", type=float, default=0.92)
    p.add_argument("--silence", type=float, default=1.2)
    p.add_argument("--eot-threshold", type=float, default=0.5)
    p.add_argument("--kyutai-stt-bits", type=int, choices=[0, 4, 8], default=8)
    p.add_argument("--kyutai-bits", type=int, choices=[0, 4, 8], default=8)
    p.add_argument("--max-context", type=int, default=16384)
    p.add_argument("--temperature", type=float, default=None)
    p.add_argument("--tutor-prompt", default=os.path.join(T.PROMPTS_DIR, "tutor.txt"))
    p.add_argument("--hear-prompt", default=os.path.join(T.PROMPTS_DIR, "hear.txt"))
    p.add_argument("--whisper-model", default="mlx-community/whisper-base-mlx")
    p.add_argument("--no-prefetch", dest="prefetch", action="store_false",
                   help="do not translate / extract vocabulary after each turn")
    p.add_argument("--no-greet", dest="greet", action="store_false", help="do not speak the greeting")
    p.add_argument("--mute", action="store_true", help="tests: play silence instead of the voice")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args()
    T.VERBOSE = args.verbose
    Engine(args).run()


if __name__ == "__main__":
    main()
