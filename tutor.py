#!/usr/bin/env python3
"""
Bavard - local French voice tutor on Apple Silicon (MLX).

Input matrix (3 x 3 x 2 = 18 combinations, the choices are independent):

  Flag      Option            What it does
  --------  ----------------  ------------------------------------------------
  --stt     audio (default)   Gemma 4 hears your voice and keeps audible errors
            whisper           MLX Whisper; fast, but often fixes your errors
            kyutai            Kyutai STT 1B; transcribes while you speak

  --turn    ptt (default)     push-to-talk: press Enter to start and to stop
            vad               hands-free: Silero VAD, ends after --silence secs
            semantic          hands-free: Kyutai STT predicts end of sentence

  --tts     kokoro (default)  Kokoro-82M; fast first audio (~2 s)
            kyutai            Kyutai TTS 1.6B q8; streamed frame by frame

  --turn semantic loads Kyutai STT for turn-taking only. With --stt audio or
  whisper, Gemma or Whisper still makes the transcript (hybrid mode).
  All Kyutai options need moshi_mlx (see README).

Quantization:

  Model               Used for       Weights             Flag               Notes
  ------------------  -------------  ------------------  -----------------  ---------------------------
  Gemma 4 E4B / E2B   tutor, STT     4-bit (MLX)         --model            fixed by the HF repo

  Gemma 4 12B         tutor, STT     4-bit QAT (MLX)     --model gemma-12b  audio input not tested

  Qwen 2.5 / Mistral  tutor          4-bit (MLX)         --model            needs --stt whisper/kyutai

  Whisper base (74M)  STT            fp16 (MLX)          --whisper-model    ~144 MB; pick another repo

  Kyutai STT 1B       STT, turn end  q8 (default)        --kyutai-stt-bits  8, 4 or 0 = bf16; q8 = bf16
                                                                            transcripts, ~0.75 GB less

  Kyutai TTS 1.6B     TTS            q8 (default)        --kyutai-bits      8, 4 or 0 = bf16; bf16 is
                                                                            ~3x slower (audio gaps)

  Kokoro-82M          TTS            fp32 (PyTorch)      -                  not quantized

  Silero VAD          turn end       fp32 (TorchScript)  -                  tiny (~2 MB)

The tutor reply has two parts: CORRECTION (shown on screen) and RÉPONSE
(spoken). RÉPONSE goes to TTS while it is still generating.

The hear stage runs without chat history. Only the text transcript enters the
history, so the history stays text-only and the KV prefix cache is reused on
every turn (TTFT stays low even at 16K context).
"""

import argparse
import json
import os
import queue
import re
import shlex
import sys
import threading
import time
import warnings

# Suppress PyTorch/HuggingFace warning noise for a clean terminal
warnings.filterwarnings("ignore")
os.environ["TOKENIZERS_PARALLELISM"] = "false"

import numpy as np
import sounddevice as sd
import mlx.core as mx
import mlx.nn as nn
from mlx_vlm import load, stream_generate
from mlx_vlm.generate.common import PromptCacheState
from mlx_vlm.prompt_utils import apply_chat_template

SAMPLE_RATE = 16000        # Gemma audio, Whisper, Silero VAD
KYUTAI_SAMPLE_RATE = 24000  # Kyutai STT (Mimi codec)
SEMANTIC_FALLBACK_S = 3.0   # --turn semantic: end the turn after this much silence anyway
MAX_AUDIO_SECONDS = 30  # Gemma 4 audio encoder limit (480,000 samples at 16 kHz)

# Install hint for moshi_mlx; targets whichever interpreter is running this script.
_PIP = f"{shlex.quote(sys.executable)} -m pip"
MOSHI_INSTALL_HINT = (
    "./run.sh installs it automatically. To install it by hand (without its pinned dependencies):\n"
    f"   {_PIP} install -r requirements.txt\n"
    f"   {_PIP} install --no-deps -r requirements-kyutai.txt"
)

MODEL_ALIASES = {
    # Gemma 4: text + native audio input (works with every --stt)
    "gemma-e4b": "mlx-community/gemma-4-e4b-it-4bit",
    "gemma-e2b": "mlx-community/gemma-4-e2b-it-4bit",
    "gemma-12b": "mlx-community/gemma-4-12B-it-qat-OptiQ-4bit",
    # Text-only models (use with --stt whisper or --stt kyutai)
    "qwen-3b": "mlx-community/Qwen2.5-3B-Instruct-4bit",
    "qwen-7b": "mlx-community/Qwen2.5-7B-Instruct-4bit",
    "mistral": "mlx-community/Mistral-7B-Instruct-v0.3-4bit",
    "mistral-7b": "mlx-community/Mistral-7B-Instruct-v0.3-4bit",
}

# System prompts live in prompts/*.txt so they can be edited without touching
# the code. The code parses these markers, so keep them in the output format:
#   hear.txt  -> "TRANSCRIPTION:" and "PRONONCIATION:"
#   tutor.txt -> "CORRECTION:" and "RÉPONSE:"
PROMPTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "prompts")
REQUIRED_MARKERS = {
    "hear": ["TRANSCRIPTION:", "PRONONCIATION:"],
    "tutor": ["CORRECTION:", "RÉPONSE:"],
}


def load_prompt(path, kind):
    """Read a system prompt file and warn if a marker the parser needs is missing."""
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read().strip()
    except OSError as e:
        sys.exit(ui(f"Impossible de lire le prompt {kind} ({path}) : {e}",
                    f"Cannot read the {kind} prompt ({path}): {e}"))
    if not text:
        sys.exit(ui(f"Le prompt {kind} est vide : {path}", f"The {kind} prompt is empty: {path}"))
    missing = [m for m in REQUIRED_MARKERS[kind] if m.lower() not in text.lower()]
    if missing:
        print(ui(f"{path} ne mentionne pas {', '.join(missing)} : le format de sortie risque de ne plus être reconnu.",
                 f"{path} does not mention {', '.join(missing)}: the output format may no longer be recognized."))
    return text


VERBOSE = False  # set by --verbose: show model loading and Hugging Face output
ENGLISH = False  # set by --en: program text in English (the tutor still speaks French)


def ui(fr, en):
    """Pick the French or English version of a program message (--en)."""
    return en if ENGLISH else fr


def log(*args, **kwargs):
    """print() that only runs with --verbose (model loading details)."""
    if VERBOSE:
        print(*args, **kwargs)


def quiet_hugging_face():
    """Hide Hugging Face progress bars ("Fetching N files") and warnings,
    such as the unauthenticated-requests notice. --verbose shows them, for
    example to follow a first-run download."""
    if VERBOSE:
        return
    from huggingface_hub.utils import disable_progress_bars, logging as hf_logging

    disable_progress_bars()
    hf_logging.set_verbosity_error()


GREETING = "Bonjour ! Comment vas-tu aujourd'hui ?"

REPLY_MARKER = re.compile(r"R[ÉE]PONSE\s*:\s*", re.IGNORECASE)
CORRECTION_MARKER = re.compile(r"^\s*CORRECTION\s*:\s*", re.IGNORECASE)
STOP_STRINGS = ["</s>", "<|im_end|>", "<|endoftext|>", "<turn|>", "<end_of_turn>"]


# ---------------------------------------------------------------------------
# Audio capture
# ---------------------------------------------------------------------------

def to_16k(audio, rate):
    """Resample a full recording to 16 kHz for Gemma, Whisper and Silero."""
    if rate == SAMPLE_RATE or not len(audio):
        return audio.astype(np.float32)
    from scipy.signal import resample_poly
    return resample_poly(audio, SAMPLE_RATE, rate).astype(np.float32)


class KyutaiListener:
    """Kyutai STT 1B (French + English), streaming, on MLX.

    Feed it 24 kHz audio while the learner speaks. It transcribes in real time
    (about 0.5 s behind the audio) and predicts the end of the turn from what
    was said, not only from silence. Like Kyutai TTS, the model is created and
    run on its own thread, because MLX binds arrays to per-thread streams.
    """

    REPO = "kyutai/stt-1b-en_fr-candle"  # the -candle weights include the end-of-turn head
    BLOCK = 1920  # one 80 ms Mimi frame at 24 kHz
    MAX_STEPS = int((MAX_AUDIO_SECONDS + 5) / 0.08)

    def __init__(self, quantize_bits=8, eot_threshold=0.5, live=True, on_partial=None):
        self.quantize_bits = quantize_bits
        self.eot_threshold = eot_threshold
        self.live = live
        # on_partial(text): called on the Kyutai thread with the transcript so
        # far, instead of printing it (used by the app sidecar).
        self.on_partial = on_partial
        self.end_of_turn = threading.Event()
        self.q = queue.Queue()
        self.pieces = []
        ready, error = threading.Event(), []
        threading.Thread(target=self._worker, args=(ready, error), daemon=True).start()
        ready.wait()
        if error:
            raise error[0]

    # -- called from any thread --------------------------------------------
    def begin(self):
        """Start a new utterance (resets the model state)."""
        self.end_of_turn.clear()
        self.q.put(("begin", None))

    def feed(self, pcm):
        """Queue 24 kHz mono float32 audio of any length."""
        self.q.put(("audio", np.asarray(pcm, dtype=np.float32).reshape(-1).copy()))

    def finish(self, flush=True):
        """End the utterance and return the transcript. flush=True first feeds
        silence to push out the last words (they lag the audio by up to ~1.3 s)."""
        done = threading.Event()
        self.q.put(("finish", (done, flush)))
        done.wait()
        return re.sub(r"\s+", " ", "".join(self.pieces)).strip()

    # -- synthesis thread ----------------------------------------------------
    def _load(self):
        try:
            import rustymimi
            import sentencepiece
            from moshi_mlx import models, utils
        except ImportError:
            sys.exit(f"Kyutai STT needs moshi_mlx. {MOSHI_INSTALL_HINT}")
        from huggingface_hub import hf_hub_download

        label = f"q{self.quantize_bits}" if self.quantize_bits else "bf16"
        log(ui(f"Initialisation de Kyutai STT 1B ({label} MLX)...", f"Loading Kyutai STT 1B ({label} MLX)..."))
        raw = json.load(open(hf_hub_download(self.REPO, "config.json")))
        mimi_weights = hf_hub_download(self.REPO, raw["mimi_name"])
        moshi_weights = hf_hub_download(self.REPO, raw.get("moshi_name", "model.safetensors"))
        tokenizer = hf_hub_download(self.REPO, raw["tokenizer_name"])

        lm_config = models.LmConfig.from_config_dict(raw)
        model = models.Lm(lm_config)
        model.set_dtype(mx.bfloat16)
        model.load_pytorch_weights(str(moshi_weights), lm_config, strict=True)
        if self.quantize_bits:
            nn.quantize(model.transformer, bits=self.quantize_bits)
        self.model = model
        self.models, self.utils = models, utils
        self.other_codebooks = lm_config.other_codebooks
        self.text_tokenizer = sentencepiece.SentencePieceProcessor(str(tokenizer))
        self.audio_tokenizer = rustymimi.Tokenizer(
            str(mimi_weights), num_codebooks=max(lm_config.generated_codebooks, lm_config.other_codebooks))
        # The config says the text lags the audio by 0.5 s, but the last word
        # often arrives ~1.25 s after speech ends. Flush up to 1.5 s of silence,
        # and stop early at the final punctuation.
        self.flush_steps = int(np.ceil(1.5 / 0.08))
        model.warmup()
        self._begin()
        self._step(np.zeros(self.BLOCK, dtype=np.float32))  # compile the step
        log(ui(f"Kyutai STT {label} prêt !\n", f"Kyutai STT {label} ready.\n"))

    def _begin(self):
        for c in self.model.transformer_cache:
            c.reset()
        self.audio_tokenizer.reset()
        self.gen = self.models.LmGen(
            model=self.model, max_steps=self.MAX_STEPS,
            text_sampler=self.utils.Sampler(top_k=25, temp=0),
            audio_sampler=self.utils.Sampler(top_k=250, temp=0.8), check=False)
        self.pieces = []
        self.buf = np.zeros(0, dtype=np.float32)
        self.steps = 0
        self.printed = False

    def _step(self, block):
        if self.steps >= self.MAX_STEPS - self.flush_steps - 1:
            return  # past the 30 s limit; Gemma cuts there too
        self.steps += 1
        tokens = self.audio_tokenizer.encode_step(block[None, None, :])
        tokens = mx.array(tokens).transpose(0, 2, 1)[:, :, : self.other_codebooks]
        text_token, heads = self.gen.step_with_extra_heads(tokens[0])
        text_token = text_token[0].item()
        if text_token not in (0, 3):  # 0 = padding, 3 = end of padding
            piece = self.text_tokenizer.id_to_piece(text_token).replace("▁", " ")
            self.pieces.append(piece)
            if self.on_partial is not None:
                self.on_partial(re.sub(r"\s+", " ", "".join(self.pieces)).strip())
            elif self.live:
                if not self.printed:
                    print("> ", end="")
                    self.printed = True
                print(piece, end="", flush=True)
        # End of turn: only after some words were heard. Before the learner
        # starts, the head can fire on silence or breathing.
        if heads and self.pieces and heads[2][0, 0, 0].item() > self.eot_threshold:
            self.end_of_turn.set()

    def _worker(self, ready, error):
        try:
            self._load()
        except BaseException as e:  # includes SystemExit from a missing dependency
            error.append(e)
            return
        finally:
            ready.set()
        while True:
            cmd, arg = self.q.get()
            try:
                if cmd == "begin":
                    self._begin()
                elif cmd == "audio":
                    self.buf = np.concatenate([self.buf, arg])
                    while len(self.buf) >= self.BLOCK:
                        block, self.buf = self.buf[: self.BLOCK], self.buf[self.BLOCK:]
                        self._step(block)
                elif cmd == "finish" and arg[1]:
                    if len(self.buf):
                        self._step(np.pad(self.buf, (0, self.BLOCK - len(self.buf))))
                        self.buf = np.zeros(0, dtype=np.float32)
                    for _ in range(self.flush_steps):
                        self._step(np.zeros(self.BLOCK, dtype=np.float32))
                        if self.pieces and self.pieces[-1].strip()[-1:] in (".", "?", "!"):
                            break
                if cmd == "finish" and self.printed and self.live and self.on_partial is None:
                    print()
                    self.printed = False
            except Exception as e:
                print(ui(f"\nErreur Kyutai STT : {e}", f"\nKyutai STT error: {e}"))
            finally:
                if cmd == "finish":
                    arg[0].set()


class PushToTalkRecorder:
    """Records between two presses of Enter. With a Kyutai listener, the audio
    is also streamed to it while you speak."""

    def __init__(self, listener=None):
        self.listener = listener
        self.rate = KYUTAI_SAMPLE_RATE if listener else SAMPLE_RATE
        self.q = queue.Queue()
        self.is_recording = False

    def _callback(self, indata, frames, time_info, status):
        if self.is_recording:
            self.q.put(indata.copy())
            if self.listener:
                self.listener.feed(indata[:, 0])

    def record(self):
        """Return the recording at 16 kHz."""
        while not self.q.empty():
            self.q.get_nowait()
        if self.listener:
            self.listener.begin()
        self.is_recording = True
        stream = sd.InputStream(samplerate=self.rate, channels=1, dtype="float32",
                                blocksize=KyutaiListener.BLOCK if self.listener else 0,
                                callback=self._callback)
        stream.start()
        try:
            input(ui("[Enregistrement...] Appuie sur [Entrée] pour terminer.\n",
                     "[Recording...] Press [Enter] to stop.\n"))
        finally:
            self.is_recording = False
            stream.stop()
            stream.close()

        chunks = []
        while not self.q.empty():
            chunks.append(self.q.get_nowait())
        if not chunks:
            return np.array([], dtype=np.float32)
        return to_16k(np.concatenate(chunks, axis=0).flatten(), self.rate)


class VadRecorder:
    """Hands-free capture. Silero VAD detects the start of speech.

    The turn ends:
    - semantic=False (--turn vad): after `silence_s` of silence. Learners pause
      often, so the default window is long (1.2 s).
    - semantic=True (--turn semantic): when Kyutai STT predicts the end of the
      turn from what was said (usually ~0.5 s after you stop, and it waits
      through "euh..."). After SEMANTIC_FALLBACK_S of silence, it ends anyway.

    With a listener, the speech (from just before it starts) is streamed to
    Kyutai STT, and capture runs at 24 kHz.
    """

    FRAME = 512  # Silero VAD frame size at 16 kHz (32 ms)

    def __init__(self, threshold=0.5, silence_s=1.2, min_speech_s=0.4, preroll_s=0.3,
                 max_s=MAX_AUDIO_SECONDS, listener=None, semantic=False):
        import torch
        from silero_vad import load_silero_vad

        if semantic and listener is None:
            raise ValueError("semantic end-of-turn needs a Kyutai listener")
        self.torch = torch
        self.model = load_silero_vad()
        self.listener = listener
        self.semantic = semantic
        self.rate = KYUTAI_SAMPLE_RATE if listener else SAMPLE_RATE
        self.frame = self.FRAME * self.rate // SAMPLE_RATE  # 512 at 16 kHz, 768 at 24 kHz
        self.threshold = threshold
        frame_s = self.FRAME / SAMPLE_RATE
        self.silence_frames = int((SEMANTIC_FALLBACK_S if semantic else silence_s) / frame_s)
        self.min_speech_frames = int(min_speech_s / frame_s)
        self.preroll_frames = int(preroll_s / frame_s)
        self.max_frames = int(max_s / frame_s)

    def _prob(self, frame):
        if self.rate != SAMPLE_RATE:
            from scipy.signal import resample_poly
            frame = resample_poly(frame, SAMPLE_RATE, self.rate).astype(np.float32)[: self.FRAME]
        return self.model(self.torch.from_numpy(np.ascontiguousarray(frame)), SAMPLE_RATE).item()

    def record(self, cancel=None, on_level=None, on_speech=None, quiet=False):
        """Return the utterance at 16 kHz.

        Embedding hooks (all optional): `cancel` is a threading.Event; when it
        is set, record() returns None. `on_level(pcm)` gets every mic block (on
        the audio thread). `on_speech()` is called when speech starts.
        """
        frames_q = queue.Queue()

        def callback(indata, frames, time_info, status):
            frames_q.put(indata[:, 0].copy())
            if on_level is not None:
                on_level(indata[:, 0])

        self.model.reset_states()
        preroll, speech = [], []
        speaking, silent_run, voiced = False, 0, 0
        buf = np.zeros(0, dtype=np.float32)
        done = lambda frames: to_16k(np.concatenate(frames), self.rate)

        with sd.InputStream(samplerate=self.rate, channels=1, dtype="float32",
                            blocksize=self.frame, callback=callback):
            if not quiet:
                print(ui("J'écoute... (parle quand tu veux, Ctrl+C pour le menu)",
                         "Listening... (speak whenever you like, Ctrl+C for the menu)"))
            while True:
                if cancel is not None:
                    if cancel.is_set():
                        return None
                    try:
                        block = frames_q.get(timeout=0.1)
                    except queue.Empty:
                        continue
                else:
                    block = frames_q.get()
                buf = np.concatenate([buf, block])
                while len(buf) >= self.frame:
                    frame, buf = buf[: self.frame], buf[self.frame:]
                    is_speech = self._prob(frame) >= self.threshold

                    if not speaking:
                        preroll.append(frame)
                        preroll = preroll[-self.preroll_frames:] if self.preroll_frames else []
                        if is_speech:
                            speaking = True
                            speech = list(preroll)
                            voiced, silent_run = 1, 0
                            if self.listener:
                                self.listener.begin()
                                self.listener.feed(np.concatenate(speech))
                            if on_speech is not None:
                                on_speech()
                            if not quiet:
                                print(ui("[Parole détectée...]", "[Speech detected...]"))
                        continue

                    speech.append(frame)
                    if self.listener:
                        self.listener.feed(frame)
                    if is_speech:
                        voiced += 1
                        silent_run = 0
                    else:
                        silent_run += 1

                    if len(speech) >= self.max_frames:
                        if not quiet:
                            print(ui(f"Limite de {MAX_AUDIO_SECONDS}s atteinte.", f"{MAX_AUDIO_SECONDS} s limit reached."))
                        return done(speech)
                    if self.semantic and self.listener.end_of_turn.is_set() and voiced >= self.min_speech_frames:
                        return done(speech)
                    if silent_run >= self.silence_frames:
                        if voiced >= self.min_speech_frames:
                            # Drop most of the trailing silence
                            keep = len(speech) - silent_run + self.preroll_frames
                            return done(speech[:keep])
                        # Too short (cough, click): go back to waiting
                        speaking, preroll, speech = False, [], []
                        self.model.reset_states()


# ---------------------------------------------------------------------------
# Text-to-speech
# ---------------------------------------------------------------------------

def clean_for_speech(text):
    text = re.sub(r"[*_#`~>|]", "", text)
    text = re.sub(r"\[[^\]]*\]", "", text)  # never speak bracketed notes
    return re.sub(r"\s+", " ", text).strip()


class KokoroSpeaker:

    def __init__(self, voice="ff_siwis", speed=0.92):
        from kokoro import KPipeline

        self.voice = voice
        self.speed = speed
        log(ui("Initialisation de Kokoro-82M...", "Loading Kokoro-82M..."))
        self.pipeline = KPipeline(lang_code="f", repo_id="hexgrad/Kokoro-82M")
        log(ui("Voix Kokoro prête !\n", "Kokoro voice ready.\n"))

    def set_speed(self, new_speed):
        self.speed = max(0.5, min(1.6, round(new_speed, 2)))

    sample_rate = 24000

    def stream(self, text, emit):
        """Synthesize `text`; call emit(pcm) for each segment Kokoro produces."""
        for _, _, audio in self.pipeline(text, voice=self.voice, speed=self.speed):
            emit((audio.numpy() if hasattr(audio, "numpy") else np.asarray(audio)).astype(np.float32))


class KyutaiSpeaker:
    # Runs on MLX. SpeechQueue builds it on its synthesis thread (see there).

    def __init__(self, voice="cml-tts/fr/10087_11650_000028-0002.wav", quantize_bits=8,
                 frame_streaming=True):
        try:
            import sentencepiece
            from moshi_mlx import models
        except ImportError:
            sys.exit(f"Kyutai TTS needs moshi_mlx. {MOSHI_INSTALL_HINT}")
        from moshi_mlx.models.tts import TTSModel, DEFAULT_DSM_TTS_REPO, DEFAULT_DSM_TTS_VOICE_REPO
        from moshi_mlx.utils.loaders import hf_get

        log(ui(f"Initialisation de Kyutai TTS 1.6B ({quantize_bits}-bit MLX)...",
               f"Loading Kyutai TTS 1.6B ({quantize_bits}-bit MLX)..."))
        raw_config = json.load(open(hf_get("config.json", DEFAULT_DSM_TTS_REPO)))
        mimi_weights = hf_get(raw_config["mimi_name"], DEFAULT_DSM_TTS_REPO)
        moshi_weights = hf_get(raw_config["moshi_name"], DEFAULT_DSM_TTS_REPO)
        tokenizer = hf_get(raw_config["tokenizer_name"], DEFAULT_DSM_TTS_REPO)

        lm_config = models.LmConfig.from_config_dict(raw_config)
        lm_config.transformer.max_seq_len = lm_config.transformer.context
        model = models.Lm(lm_config)
        model.set_dtype(mx.bfloat16)
        model.load_pytorch_weights(str(moshi_weights), lm_config, strict=True)

        if quantize_bits:
            log(ui(f"Quantification MLX en {quantize_bits}-bit...", f"Quantizing to {quantize_bits}-bit MLX..."))
            nn.quantize(model.depformer, bits=quantize_bits)
            for layer in model.transformer.layers:
                nn.quantize(layer.self_attn, bits=quantize_bits)
                nn.quantize(layer.gating, bits=quantize_bits)

        text_tokenizer = sentencepiece.SentencePieceProcessor(str(tokenizer))
        audio_tokenizer = models.mimi.Mimi(models.mimi_202407(lm_config.generated_codebooks))
        audio_tokenizer.load_pytorch_weights(str(mimi_weights), strict=True)

        self.tts_model = TTSModel(
            model,
            audio_tokenizer,
            text_tokenizer,
            voice_repo=DEFAULT_DSM_TTS_VOICE_REPO,
            raw_config=raw_config,
        )
        self.cfg_coef_conditioning = self.tts_model.cfg_coef
        self.tts_model.cfg_coef = 1.0
        self.voice = voice
        self.speed = 1.0
        self.frame_streaming = frame_streaming
        self.sample_rate = self.tts_model.mimi.sample_rate
        tts_label = f"q{quantize_bits}" if quantize_bits else "bf16"
        log(ui(f"Kyutai TTS {tts_label} prêt !\n", f"Kyutai TTS {tts_label} ready.\n"))

    def set_speed(self, new_speed):
        self.speed = max(0.5, min(1.6, round(new_speed, 2)))

    def stream(self, text, emit):
        """Synthesize `text`; call emit(pcm) as audio becomes available.

        With frame streaming, each 80 ms Mimi frame is decoded as soon as the
        model produces it, so playback starts after the first frames instead of
        after the whole chunk.
        """
        all_entries = [self.tts_model.prepare_script([text])]
        voices = [self.tts_model.get_voice_path(self.voice)] if self.tts_model.multi_speaker else []
        all_attributes = [self.tts_model.make_condition_attributes(voices, self.cfg_coef_conditioning)]
        mimi = self.tts_model.mimi

        on_frame = None
        if self.frame_streaming:
            def on_frame(frame):
                if (frame == -1).any():
                    return
                pcm = mimi.decode_step(frame[:, :, None])
                emit(np.array(mx.clip(pcm[0, 0], -1, 1), dtype=np.float32))

        res = self.tts_model.generate(all_entries, all_attributes, cfg_is_no_prefix=False,
                                      cfg_is_no_text=False, on_frame=on_frame)
        if not self.frame_streaming and res.frames:
            pcm = mimi.decode(mx.concat(res.frames, axis=-1))
            emit(np.array(mx.clip(pcm[0, 0], -1, 1), dtype=np.float32))


class PcmPlayer:
    """One continuous output stream fed with PCM pieces of any size.

    A small jitter buffer (`prebuffer_s`) is filled before playback starts or
    resumes, so frame-by-frame TTS does not crackle on small timing hiccups.
    """

    def __init__(self, sample_rate, prebuffer_s=0.0):
        self.sample_rate = sample_rate
        self.prebuffer = int(prebuffer_s * sample_rate)
        self.lock = threading.Lock()
        self.pieces = []  # list of np.float32 arrays
        self.buffered = 0
        self.playing = False
        self.flushed = False  # True when no more audio is coming for now
        self.on_first_sound = None
        self.on_level = None  # on_level(pcm): every output block, on the audio thread
        self.stream = sd.OutputStream(samplerate=sample_rate, channels=1, dtype="float32",
                                      blocksize=480, callback=self._callback)
        self.stream.start()

    def write(self, pcm):
        if pcm is None or not len(pcm):
            return
        with self.lock:
            self.pieces.append(pcm)
            self.buffered += len(pcm)
            self.flushed = False

    def flush(self):
        with self.lock:
            self.flushed = True

    def pending(self):
        with self.lock:
            return self.buffered

    def clear(self):
        """Drop everything not played yet (used to stop the tutor mid-sentence)."""
        with self.lock:
            self.pieces = []
            self.buffered = 0
            self.playing = False
            self.flushed = True

    def _callback(self, outdata, frames, time_info, status):
        out = outdata[:, 0]
        out[:] = 0
        with self.lock:
            if not self.playing:
                if self.buffered == 0 or (self.buffered < self.prebuffer and not self.flushed):
                    return
                self.playing = True
            filled = 0
            while filled < frames and self.pieces:
                piece = self.pieces[0]
                n = min(frames - filled, len(piece))
                out[filled:filled + n] = piece[:n]
                filled += n
                if n == len(piece):
                    self.pieces.pop(0)
                else:
                    self.pieces[0] = piece[n:]
            self.buffered -= filled
            if self.buffered == 0:
                self.playing = False  # underrun or end: re-arm the jitter buffer
        if self.on_level is not None:
            self.on_level(out)
        if filled and self.on_first_sound is not None:
            cb, self.on_first_sound = self.on_first_sound, None
            cb()


class SpeechQueue:
    """Synthesis thread -> continuous player, so synthesis of chunk N+1 overlaps
    playback of chunk N. Tracks time to first audio for each turn.

    The speaker is built *inside* the synthesis thread. MLX binds arrays to
    per-thread streams, so an MLX TTS model (Kyutai) must be created on the
    thread that runs it. Built that way, it can synthesize while the LLM is
    still generating on the main thread.
    """

    def __init__(self, make_speaker, prebuffer_s=0.0):
        self.speaker = None
        self.text_q = queue.Queue()
        self.turn_start = None
        self.first_audio_at = None
        self.epoch = 0  # bumped by cancel(); audio from older epochs is dropped
        ready, error = threading.Event(), []
        threading.Thread(target=self._synth_worker, args=(make_speaker, ready, error),
                         daemon=True).start()
        ready.wait()
        if error:
            raise error[0]
        self.player = PcmPlayer(self.speaker.sample_rate, prebuffer_s)

    def begin_turn(self, t0):
        self.turn_start = t0
        self.first_audio_at = None
        if t0 is not None:
            self.player.on_first_sound = self._mark_first_sound

    def _mark_first_sound(self):
        self.first_audio_at = time.time() - self.turn_start + (self.player.stream.latency or 0)

    def say(self, text):
        text = clean_for_speech(text)
        if text:
            self.text_q.put(text)

    def cancel(self):
        """Stop speaking now: drop queued text and unplayed audio."""
        self.epoch += 1
        while True:
            try:
                self.text_q.get_nowait()
            except queue.Empty:
                break
            self.text_q.task_done()
        self.player.clear()

    def wait(self):
        self.text_q.join()
        self.player.flush()
        while self.player.pending():
            time.sleep(0.02)
        time.sleep(self.player.stream.latency or 0)  # let the device drain

    def _synth_worker(self, make_speaker, ready, error):
        try:
            self.speaker = make_speaker()
        except BaseException as e:  # includes SystemExit from a missing dependency
            error.append(e)
            return
        finally:
            ready.set()
        while True:
            text = self.text_q.get()
            epoch = self.epoch

            def write(pcm, epoch=epoch):
                if self.epoch == epoch:
                    self.player.write(pcm)

            try:
                self.speaker.stream(text, write)
            except Exception as e:  # keep the session alive on a TTS error
                print(ui(f"\nErreur TTS : {e}", f"\nTTS error: {e}"))
            finally:
                if self.text_q.unfinished_tasks == 1:
                    self.player.flush()  # nothing else queued: play what we have
                self.text_q.task_done()


class Chunker:
    """Splits streamed reply text into TTS chunks.

    First chunk: cut at the first comma/semicolon/colon or sentence end, so audio
    starts after a few words. Later chunks: cut at sentence ends.
    """

    FIRST_SPLIT = re.compile(r"[,;:.!?…](\s|$)")
    SENTENCE_SPLIT = re.compile(r"[.!?…](\s|$)")

    def __init__(self, emit, min_first_words=3):
        self.emit = emit
        self.buf = ""
        self.first = True
        self.min_first_words = min_first_words

    def feed(self, text):
        self.buf += text
        while True:
            pattern = self.FIRST_SPLIT if self.first else self.SENTENCE_SPLIT
            cut = None
            for m in pattern.finditer(self.buf):
                head = self.buf[: m.end()]
                if not self.first or len(head.split()) >= self.min_first_words:
                    cut = m.end()
                    break
            # Need at least one char after the punctuation to be sure it is a
            # boundary (e.g. not "3." of "3.5") unless the stream has ended.
            if cut is None or cut >= len(self.buf):
                return
            self.emit(self.buf[:cut].strip())
            self.buf = self.buf[cut:]
            self.first = False

    def flush(self):
        if self.buf.strip():
            self.emit(self.buf.strip())
        self.buf = ""


# ---------------------------------------------------------------------------
# LLM
# ---------------------------------------------------------------------------

class Tutor:
    def __init__(self, model_repo, max_context, temperature=None, tutor_prompt="", hear_prompt=""):
        self.tutor_prompt = tutor_prompt
        self.hear_prompt = hear_prompt
        name = model_repo.split("/")[-1]
        log(ui(f"Chargement du modèle {name}...", f"Loading model {name}..."))
        self.model, self.processor = load(model_repo)
        self.tokenizer = getattr(self.processor, "tokenizer", self.processor)
        cfg = self.model.config
        cfg_dict = cfg if isinstance(cfg, dict) else vars(cfg)
        self.supports_audio = any(k in cfg_dict for k in ("audio_config", "audio_token_id"))
        self.is_gemma = "gemma" in model_repo.lower()
        # Google's recommended sampling for Gemma 4; a calmer default otherwise.
        self.temperature = temperature if temperature is not None else (1.0 if self.is_gemma else 0.7)
        self.sampling = {"top_p": 0.95, "top_k": 64} if self.is_gemma else {}
        self.max_context = max_context
        self.cache = PromptCacheState()
        self.history = []  # text-only chat history (no system message)
        self.merge_system = False
        self.last_stats = None
        log(ui("Modèle LLM prêt !\n", "LLM ready.\n"))

    # -- prompt building ---------------------------------------------------
    def _template(self, messages, num_audios=0, add_generation_prompt=True):
        if self.merge_system and messages and messages[0]["role"] == "system":
            # Some templates (e.g. Mistral v0.3) reject a system role.
            sys_msg, rest = messages[0]["content"], [dict(m) for m in messages[1:]]
            for m in rest:
                if m["role"] == "user":
                    m["content"] = f"{sys_msg}\n\n{m['content']}"
                    break
            messages = rest
        try:
            return apply_chat_template(self.processor, self.model.config, messages, num_audios=num_audios,
                                       add_generation_prompt=add_generation_prompt)
        except Exception:
            if self.merge_system:
                raise
            self.merge_system = True
            return self._template(messages, num_audios, add_generation_prompt)

    def _tutor_messages(self):
        return [{"role": "system", "content": self.tutor_prompt}] + self.history

    def context_tokens(self):
        return len(self.tokenizer.encode(self._template(self._tutor_messages())))

    def _trim_history(self):
        """Keep the history under budget. Trims down to ~75% so the (one-time)
        cold prefill after a trim is rare, instead of happening every turn."""
        budget = self.max_context - 400  # room for the reply
        if self.context_tokens() <= budget:
            return
        target = int(budget * 0.75)
        while len(self.history) > 2 and self.context_tokens() > target:
            self.history.pop(0)
            while self.history and self.history[0]["role"] != "user":
                self.history.pop(0)

    # -- stages ------------------------------------------------------------
    def hear(self, audio):
        """Gemma native audio -> (transcript, pronunciation note). No history."""
        messages = [
            {"role": "system", "content": self.hear_prompt},
            {"role": "user", "content": "Transcris cet enregistrement de l'élève."},
        ]
        prompt = self._template(messages, num_audios=1)
        out = ""
        for r in stream_generate(self.model, self.processor, prompt, audio=[audio],
                                 max_tokens=150, temperature=0.0):
            out += r.text
        out = strip_stops(out)
        transcript = _field(out, "TRANSCRIPTION") or out.strip()
        pron = _field(out, "PRONONCIATION")
        if pron and pron.strip().upper().rstrip(".") in ("RAS", "AUCUN", "AUCUNE", "NONE"):
            pron = None
        return transcript, pron

    def reply(self, user_content, on_text):
        """Stream the tutor reply over the cached history. Calls on_text(delta)."""
        self.history.append({"role": "user", "content": user_content})
        self._trim_history()
        prompt = self._template(self._tutor_messages())

        out, last, t0, ttft = "", None, time.time(), None
        for r in stream_generate(self.model, self.processor, prompt, max_tokens=220,
                                 temperature=self.temperature, prompt_cache_state=self.cache,
                                 **self.sampling):
            if ttft is None:
                ttft = time.time() - t0
            out += r.text
            last = r
            if any(s in out for s in STOP_STRINGS):
                break
            on_text(r.text)

        out = strip_stops(out)
        # Store the raw output so the next prompt extends the cached tokens exactly.
        self.history.append({"role": "assistant", "content": out})
        if last is not None:
            self.last_stats = {
                "ttft": ttft or 0.0,
                "prompt_tokens": last.prompt_tokens,
                "cached_tokens": last.cached_tokens,
                "gen_tokens": last.generation_tokens,
                "tg_tps": last.generation_tps,
                "peak_gb": last.peak_memory,
            }
        return out

    def oneshot(self, system, user, max_tokens=300, on_text=None):
        """A side request that never touches the tutor history or its prefix
        cache (used for translations and vocabulary). Gemma's sliding-window
        cache cannot be rolled back, so these must not use self.cache."""
        prompt = self._template([{"role": "system", "content": system},
                                 {"role": "user", "content": user}])
        out = ""
        for r in stream_generate(self.model, self.processor, prompt, max_tokens=max_tokens,
                                 temperature=0.0):
            out += r.text
            if any(s in out for s in STOP_STRINGS):
                break
            if on_text is not None:
                on_text(out)
        return strip_stops(out)

    def seed_greeting(self, greeting):
        # Gemma templates expect the first turn to come from the user.
        self.history = [
            {"role": "user", "content": "TRANSCRIPTION: (début de la séance)"},
            {"role": "assistant", "content": f"CORRECTION: RAS\nRÉPONSE: {greeting}"},
        ]

    def warm_up(self, audio=False):
        """Compile kernels and prefill the system prompt + greeting into the
        prefix cache, so the first real turn is as fast as later ones."""
        if audio:
            self.hear(np.zeros(SAMPLE_RATE, dtype=np.float32))
        # The cached tokens must be an exact prefix of the first real prompt.
        # Gemma's sliding-window KV caches cannot be rolled back once the prompt
        # is longer than the window (512 tokens), so any divergence would force a
        # cold prefill on turn 1. Generation always adds one token to the cache,
        # so force that token to be the real next token of the next prompt.
        prefix = self._template(self._tutor_messages(), add_generation_prompt=False)
        nxt = self._template(self._tutor_messages() + [{"role": "user", "content": "TRANSCRIPTION: x"}])
        prefix_ids = self.tokenizer.encode(prefix)
        next_ids = self.tokenizer.encode(nxt)
        if next_ids[: len(prefix_ids)] != prefix_ids or len(next_ids) <= len(prefix_ids):
            prefix, forced = self._template(self._tutor_messages()), None  # fallback: plain warm-up
        else:
            forced = next_ids[len(prefix_ids)]

        def force_next(tokens, logits):
            mask = mx.full(logits.shape, -mx.inf, dtype=logits.dtype)
            mask[..., forced] = 0
            return logits + mask

        for _ in stream_generate(self.model, self.processor, prefix, max_tokens=1, temperature=0.0,
                                 prompt_cache_state=self.cache,
                                 logits_processors=[force_next] if forced is not None else None):
            pass


def strip_stops(text):
    for s in STOP_STRINGS:
        text = text.split(s)[0]
    return text.strip()


def _field(text, name):
    m = re.search(rf"{name}\s*:\s*(.*)", text, re.IGNORECASE)
    return m.group(1).strip() if m else None


def split_reply(text):
    """-> (correction, spoken reply). Tolerates a missing RÉPONSE marker."""
    m = REPLY_MARKER.search(text)
    if not m:
        lines = [l for l in text.splitlines() if not CORRECTION_MARKER.match(l)]
        return None, " ".join(lines).strip()
    correction = CORRECTION_MARKER.sub("", text[: m.start()]).strip()
    return correction, text[m.end():].strip()


class ReplyStreamer:
    """Routes streamed tutor output: everything after 'RÉPONSE:' goes to TTS."""

    def __init__(self, speech):
        self.text = ""
        self.spoken_upto = None
        self.chunker = Chunker(speech.say)

    def __call__(self, delta):
        self.text += delta
        print(delta, end="", flush=True)
        if self.spoken_upto is None:
            m = REPLY_MARKER.search(self.text)
            if not m:
                return
            self.spoken_upto = m.end()
        new = self.text[self.spoken_upto:]
        self.spoken_upto = len(self.text)
        if new:
            self.chunker.feed(new)

    def finish(self):
        if self.spoken_upto is None:
            # No marker: speak the whole reply minus any correction line.
            _, reply = split_reply(self.text)
            self.chunker.feed(reply)
        self.chunker.flush()


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

class QuitRequested(Exception):
    """The learner typed q / quit / exit."""


def handle_command(cmd, speech, tutor, last_reply):
    """Returns True if `cmd` was a command (and was handled)."""
    speaker = speech.speaker
    if cmd == "+":
        speaker.set_speed(speaker.speed + 0.05)
        print(ui(f"Vitesse augmentée à {speaker.speed:.2f}x\n", f"Speed up to {speaker.speed:.2f}x\n"))
    elif cmd == "-":
        speaker.set_speed(speaker.speed - 0.05)
        print(ui(f"Vitesse ralentie à {speaker.speed:.2f}x\n", f"Speed down to {speaker.speed:.2f}x\n"))
    elif cmd in ("r", "repeat", "repeter", "replay"):
        print(ui(f"Répétition : \"{last_reply}\"", f"Repeating: \"{last_reply}\""))
        speech.begin_turn(None)
        for chunk in re.split(r"(?<=[.!?…])\s+", last_reply):
            speech.say(chunk)
        speech.wait()
        print()
    elif cmd in ("tokens", "stats", "status"):
        turns = sum(1 for m in tutor.history if m["role"] == "user")
        used = f"{tutor.context_tokens():,} / {tutor.max_context:,} tokens"
        plural = "s" if turns != 1 else ""
        print(ui(f"Contexte: {used} ({turns} tour{plural})", f"Context: {used} ({turns} turn{plural})"))
        s = tutor.last_stats
        if s:
            cached = f"{s['cached_tokens']}/{s['prompt_tokens']}"
            print(ui(f"   Dernier tour: TTFT {s['ttft']:.2f}s, {cached} tokens en cache, "
                     f"{s['tg_tps']:.1f} tok/s, pic mémoire {s['peak_gb']:.2f} GB",
                     f"   Last turn: TTFT {s['ttft']:.2f}s, {cached} tokens cached, "
                     f"{s['tg_tps']:.1f} tok/s, peak memory {s['peak_gb']:.2f} GB"))
        print()
    else:
        try:
            speaker.set_speed(float(cmd.replace(",", ".")))
            print(ui(f"Vitesse réglée à {speaker.speed:.2f}x\n", f"Speed set to {speaker.speed:.2f}x\n"))
        except ValueError:
            return False
    return True


def main():
    parser = argparse.ArgumentParser(description="Bavard - French voice tutor with Apple MLX")
    parser.add_argument("--stt", "--input", dest="stt", choices=["audio", "whisper", "kyutai"], default="audio",
                        help="'audio' : Gemma 4 entend ta voix directement (défaut). "
                             "'whisper' : transcription par MLX Whisper. "
                             "'kyutai' : transcription en direct par Kyutai STT 1B.")
    parser.add_argument("--turn", choices=["ptt", "vad", "semantic"], default="ptt",
                        help="'ptt' : push-to-talk avec Entrée (défaut). "
                             "'vad' : mains libres, fin du tour après un silence (Silero VAD). "
                             "'semantic' : mains libres, Kyutai STT détecte la fin de ta phrase.")
    parser.add_argument("--silence", type=float, default=1.2,
                        help="--turn vad : secondes de silence avant la fin du tour (défaut: 1.2)")
    parser.add_argument("--eot-threshold", type=float, default=0.5,
                        help="--turn semantic : seuil de fin de tour Kyutai, 0-1 (défaut: 0.5 ; plus haut = attend plus)")
    parser.add_argument("--kyutai-stt-bits", type=int, choices=[0, 4, 8], default=8,
                        help="Kyutai STT : quantification MLX (défaut: 8 = q8 ; 0 = bf16)")
    parser.add_argument("--model", default="gemma-e4b",
                        help="LLM : 'gemma-e4b' (défaut), 'gemma-e2b', 'gemma-12b', "
                             "'qwen-3b', 'qwen-7b', 'mistral' ou un repo HF")
    parser.add_argument("--tts", choices=["kokoro", "kyutai"], default="kokoro",
                        help="Moteur vocal : 'kokoro' (rapide, streaming) ou 'kyutai' (Kyutai 1.6B q8)")
    parser.add_argument("--kyutai-bits", type=int, choices=[0, 4, 8], default=8,
                        help="Kyutai TTS : quantification MLX (défaut: 8 = q8 ; 0 = bf16)")
    parser.add_argument("--no-frame-streaming", action="store_true",
                        help="Kyutai TTS : attendre la fin de chaque morceau avant de le jouer")
    parser.add_argument("--voice", default="default", help="Voix TTS")
    parser.add_argument("--speed", type=float, default=0.92, help="Vitesse de parole (défaut: 0.92)")
    parser.add_argument("--max-context", type=int, default=16384, help="Taille max du contexte en tokens")
    parser.add_argument("--temperature", type=float, default=None,
                        help="Température du tuteur (défaut: 1.0 pour Gemma, 0.7 sinon)")
    parser.add_argument("--tutor-prompt", default=os.path.join(PROMPTS_DIR, "tutor.txt"),
                        help="Fichier du prompt système du tuteur (défaut: prompts/tutor.txt)")
    parser.add_argument("--hear-prompt", default=os.path.join(PROMPTS_DIR, "hear.txt"),
                        help="Mode audio : fichier du prompt de transcription (défaut: prompts/hear.txt)")
    parser.add_argument("--whisper-model", default="mlx-community/whisper-base-mlx",
                        help="--stt whisper : modèle MLX Whisper (défaut: whisper-base-mlx)")
    parser.add_argument("--en", action="store_true",
                        help="Program text in English (the tutor still speaks French)")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="Afficher le chargement des modèles et les messages Hugging Face")
    args = parser.parse_args()
    global VERBOSE, ENGLISH
    VERBOSE = args.verbose
    ENGLISH = args.en

    model_repo = MODEL_ALIASES.get(args.model.lower(), args.model)

    print("\n" + "=" * 60)
    print(ui("BAVARD - TUTEUR DE FRANÇAIS (Apple Silicon MLX)", "BAVARD - FRENCH TUTOR (Apple Silicon MLX)"))
    print("=" * 60)
    print(f"• LLM:      {model_repo}")
    stt_label = f"q{args.kyutai_stt_bits}" if args.kyutai_stt_bits else "bf16"
    print(ui("• Écoute:   ", "• STT:      ") + {
        "audio": ui("Gemma audio natif", "Gemma native audio"),
        "whisper": f"Whisper {args.whisper_model}",
        "kyutai": f"Kyutai STT 1B {stt_label} (streaming)"}[args.stt])
    print(ui("• Tour:     ", "• Turn:     ") + {
        "ptt": ui("Push-to-talk (Entrée)", "Push-to-talk (Enter)"),
        "vad": ui(f"Mains libres (Silero VAD, silence {args.silence}s)",
                  f"Hands-free (Silero VAD, {args.silence} s silence)"),
        "semantic": ui(f"Mains libres (fin de phrase Kyutai STT {stt_label})",
                       f"Hands-free (Kyutai STT {stt_label} end of sentence)")}[args.turn])
    kyutai_label = f"q{args.kyutai_bits}" if args.kyutai_bits else "bf16"
    print(f"• TTS:      {'Kyutai 1.6B ' + kyutai_label if args.tts == 'kyutai' else 'Kokoro-82M (streaming)'}")
    print(ui("• Contexte: ", "• Context:  ") + f"{args.max_context:,} tokens")
    def show(path):
        rel = os.path.relpath(path)
        return path if rel.startswith("..") else rel
    print(f"• Prompts:  {show(args.tutor_prompt)}" + (f", {show(args.hear_prompt)}" if args.stt == "audio" else ""))
    print("=" * 60 + "\n")
    if not VERBOSE:
        # Loading is silent without --verbose, so say so before the wait.
        print(ui("Chargement...", "Loading..."), flush=True)

    tutor_prompt = load_prompt(args.tutor_prompt, "tutor")
    hear_prompt = load_prompt(args.hear_prompt, "hear") if args.stt == "audio" else ""
    quiet_hugging_face()
    tutor = Tutor(model_repo, args.max_context, args.temperature, tutor_prompt, hear_prompt)
    if args.stt == "audio" and not tutor.supports_audio:
        sys.exit(ui(f"{model_repo} n'accepte pas l'audio. Utilise --stt whisper, --stt kyutai ou un modèle Gemma 4.",
                    f"{model_repo} does not accept audio. Use --stt whisper, --stt kyutai or a Gemma 4 model."))

    if args.stt == "whisper":
        import mlx_whisper
        log(ui(f"Chargement de Whisper ({args.whisper_model})...", f"Loading Whisper ({args.whisper_model})..."))
        mlx_whisper.transcribe(np.zeros(SAMPLE_RATE, dtype=np.float32),
                               path_or_hf_repo=args.whisper_model, language="fr")
        log(ui("Whisper prêt !\n", "Whisper ready.\n"))

    if args.tts == "kyutai":
        voice = "cml-tts/fr/10087_11650_000028-0002.wav" if args.voice == "default" else args.voice
        make_speaker = lambda: KyutaiSpeaker(voice=voice, quantize_bits=args.kyutai_bits,
                                             frame_streaming=not args.no_frame_streaming)
        prebuffer = 0.0 if args.no_frame_streaming else 0.24  # 3 Mimi frames
    else:
        voice = "ff_siwis" if args.voice == "default" else args.voice
        make_speaker = lambda: KokoroSpeaker(voice=voice, speed=args.speed)
        prebuffer = 0.0
    try:
        speech = SpeechQueue(make_speaker, prebuffer_s=prebuffer)
    except SystemExit:
        raise
    except Exception as e:
        sys.exit(ui(f"Impossible de charger le TTS : {e}", f"Cannot load the TTS: {e}"))
    speaker = speech.speaker

    listener = None
    if args.stt == "kyutai" or args.turn == "semantic":
        try:
            listener = KyutaiListener(quantize_bits=args.kyutai_stt_bits, eot_threshold=args.eot_threshold)
        except SystemExit:
            raise
        except Exception as e:
            sys.exit(ui(f"Impossible de charger Kyutai STT : {e}", f"Cannot load Kyutai STT: {e}"))

    if args.turn in ("vad", "semantic"):
        log(ui("Chargement de Silero VAD...", "Loading Silero VAD..."))
        recorder = VadRecorder(silence_s=args.silence, listener=listener, semantic=args.turn == "semantic")
        log(ui("VAD prêt ! (utilise un casque pour éviter l'écho)\n", "VAD ready. (use headphones to avoid echo)\n"))
    else:
        recorder = PushToTalkRecorder(listener=listener)

    tutor.seed_greeting(GREETING)
    log(ui("Préchauffage...", "Warming up..."))
    tutor.warm_up(audio=args.stt == "audio")
    last_reply = GREETING
    print(ui("Tuteur: ", "Tutor: ") + f"{GREETING}\n")
    speech.say(GREETING)
    speech.wait()

    print(ui("Commandes :", "Commands:"))
    if args.turn == "ptt":
        print(ui("   • [Entrée]    : Parler (puis Entrée pour terminer)",
                 "   • [Enter]     : Speak (then Enter to stop)"))
    else:
        print(ui("   • Parle simplement ; Ctrl+C ouvre le menu (Entrée pour reprendre)",
                 "   • Just speak; Ctrl+C opens the menu (Enter to resume)"))
    print(ui("   • [+] ou [-]  : Accélérer ou ralentir la voix (ou un nombre, ex. 0.8)",
             "   • [+] or [-]  : Speed up or slow down the voice (or a number, e.g. 0.8)"))
    print(ui("   • [r]         : Répéter la dernière réponse",
             "   • [r]         : Repeat the last reply"))
    print(ui("   • [stats]     : Contexte, cache et vitesse",
             "   • [stats]     : Context, cache and speed"))
    print(ui("   • [q] / Ctrl+C: Quitter\n",
             "   • [q] / Ctrl+C: Quit\n"))

    while True:
        try:
            # ---- 1. capture -------------------------------------------------
            if args.turn == "ptt":
                cmd = input(ui(f"[Entrée] Parler | [+]/[-] Vitesse | [r] Répéter ({speaker.speed:.2f}x) : ",
                               f"[Enter] Speak | [+]/[-] Speed | [r] Repeat ({speaker.speed:.2f}x): ")).strip().lower()
                if cmd in ("q", "quit", "exit"):
                    raise QuitRequested
                if cmd and handle_command(cmd, speech, tutor, last_reply):
                    continue
                audio = recorder.record()
            else:
                try:
                    audio = recorder.record()
                except KeyboardInterrupt:
                    cmd = input(ui(f"\nPause. [Entrée] Reprendre | [+]/[-] | [r] | [stats] | [q] Quitter ({speaker.speed:.2f}x) : ",
                                   f"\nPaused. [Enter] Resume | [+]/[-] | [r] | [stats] | [q] Quit ({speaker.speed:.2f}x): ")).strip().lower()
                    if cmd in ("q", "quit", "exit"):
                        raise QuitRequested
                    if cmd:
                        handle_command(cmd, speech, tutor, last_reply)
                    continue

            t_end = time.time()
            duration = len(audio) / SAMPLE_RATE
            if duration < 0.5 or np.max(np.abs(audio)) < 0.01:
                if listener:
                    listener.finish(flush=False)
                print(ui("Enregistrement trop court ou silencieux. Réessaie !", "Recording too short or silent. Try again."))
                continue
            if duration > MAX_AUDIO_SECONDS:
                print(ui(f"Audio coupé à {MAX_AUDIO_SECONDS}s.", f"Audio cut at {MAX_AUDIO_SECONDS} s."))
                audio = audio[: SAMPLE_RATE * MAX_AUDIO_SECONDS]

            # ---- 2. hear ----------------------------------------------------
            pron = None
            if listener and args.stt != "kyutai":
                listener.finish(flush=False)  # end the live transcript line
            if args.stt == "audio":
                print(ui("Écoute (Gemma audio)...", "Listening (Gemma audio)..."))
                transcript, pron = tutor.hear(audio)
            elif args.stt == "whisper":
                print(ui("Transcription (Whisper)...", "Transcribing (Whisper)..."))
                transcript = mlx_whisper.transcribe(audio, path_or_hf_repo=args.whisper_model,
                                                    language="fr").get("text", "").strip()
            else:
                transcript = listener.finish()  # already transcribed while you spoke
            t_heard = time.time()

            if not transcript or "(inaudible)" in transcript.lower():
                print(ui("Rien compris. Réessaie !", "Nothing understood. Try again."))
                continue

            print(ui("\nToi: ", "\nYou: ") + transcript)
            if pron:
                print(ui("Prononciation: ", "Pronunciation: ") + pron)
            user_content = f"TRANSCRIPTION: {transcript}"
            if pron:
                user_content += f"\nPRONONCIATION: {pron}"

            # ---- 3. tutor + 4. streaming speech ----------------------------
            print(ui("Tuteur:", "Tutor:"))
            speech.begin_turn(t_end)
            streamer = ReplyStreamer(speech)
            raw = tutor.reply(user_content, streamer)
            streamer.finish()
            print()

            _, spoken = split_reply(raw)
            last_reply = spoken or last_reply

            speech.wait()  # in VAD mode, do not listen while the tutor talks
            s = tutor.last_stats or {}
            first = f"{speech.first_audio_at:.2f}s" if speech.first_audio_at else "n/a"
            cached = f"{s.get('cached_tokens', 0)}/{s.get('prompt_tokens', 0)}"
            print(ui(f"écoute {t_heard - t_end:.2f}s | TTFT {s.get('ttft', 0):.2f}s ({cached} en cache) | "
                     f"{s.get('tg_tps', 0):.0f} tok/s | 1er son {first}\n",
                     f"hear {t_heard - t_end:.2f}s | TTFT {s.get('ttft', 0):.2f}s ({cached} cached) | "
                     f"{s.get('tg_tps', 0):.0f} tok/s | first audio {first}\n"))

        except (QuitRequested, KeyboardInterrupt) as e:
            # Ctrl+C leaves the cursor on the "^C" line; a typed "q" already ended its line.
            print("\n" if isinstance(e, KeyboardInterrupt) else "", end="")
            print(ui("\nAu revoir et à bientôt !", "\nGoodbye, see you soon!"))
            break


if __name__ == "__main__":
    main()
