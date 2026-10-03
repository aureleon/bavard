#!/usr/bin/env python3
"""
Local French Voice Tutor using Apple MLX.
- STT: Apple MLX Whisper (runs on Apple Silicon GPU/ANE)
- LLM: Qwen2.5-3B (fast decode ~48 tok/s) or Mistral-7B (Apple MLX)
- TTS: Kyutai TTS 1.6B (8-bit MLX) or Kokoro-82M (fast neural)
"""

import argparse
import json
import os
import queue
import sys
import time
import warnings

# Suppress PyTorch/HuggingFace warning noise for a clean terminal
warnings.filterwarnings("ignore")
os.environ["TOKENIZERS_PARALLELISM"] = "false"

import numpy as np
import sounddevice as sd
import mlx.core as mx
import mlx.nn as nn
import mlx_whisper
from mlx_lm import load, generate

SYSTEM_PROMPT = """Tu es un tuteur de français bienveillant et encourageant pour un élève débutant (niveau A1/A2).
Directives strictes :
1. Réponds TOUJOURS en français simple, naturel et concis (1 à 2 phrases courtes maximum).
2. Si l'élève a fait une faute de grammaire ou de vocabulaire importante, commence par lui donner la correction de façon douce (ex: "On dit plutôt : '...'").
3. Termine toujours ta réponse par une question simple et ouverte pour relancer la conversation.
4. Reste amical et dynamique."""

MODEL_ALIASES = {
    "qwen-3b": "mlx-community/Qwen2.5-3B-Instruct-4bit",
    "qwen-7b": "mlx-community/Qwen2.5-7B-Instruct-4bit",
    "mistral": "mlx-community/Mistral-7B-Instruct-v0.3-4bit",
    "mistral-7b": "mlx-community/Mistral-7B-Instruct-v0.3-4bit",
}


class AudioRecorder:
    def __init__(self, sample_rate=16000):
        self.sample_rate = sample_rate
        self.q = queue.Queue()
        self.is_recording = False
        self.stream = None

    def _callback(self, indata, frames, time_info, status):
        if self.is_recording:
            self.q.put(indata.copy())

    def start(self):
        while not self.q.empty():
            self.q.get_nowait()
        self.is_recording = True
        self.stream = sd.InputStream(
            samplerate=self.sample_rate,
            channels=1,
            dtype="float32",
            callback=self._callback,
        )
        self.stream.start()

    def stop(self):
        self.is_recording = False
        if self.stream:
            self.stream.stop()
            self.stream.close()
            self.stream = None

        chunks = []
        while not self.q.empty():
            chunks.append(self.q.get_nowait())

        if not chunks:
            return np.array([], dtype=np.float32)
        return np.concatenate(chunks, axis=0).flatten()


class KokoroSpeaker:
    def __init__(self, voice="ff_siwis", speed=0.92):
        from kokoro import KPipeline
        self.voice = voice
        self.speed = speed
        print("🎙️  Initialisation de Kokoro-82M...")
        self.pipeline = KPipeline(lang_code="f", repo_id="hexgrad/Kokoro-82M")
        print("✅ Voix Kokoro prête !\n")

    def set_speed(self, new_speed):
        self.speed = max(0.5, min(1.6, round(new_speed, 2)))

    def speak(self, text):
        clean_text = text.replace('"', '\\"').replace("\n", " ").strip()
        if not clean_text:
            return

        generator = self.pipeline(clean_text, voice=self.voice, speed=self.speed)
        for _, _, audio in generator:
            arr = audio.numpy() if hasattr(audio, "numpy") else np.array(audio)
            sd.play(arr, samplerate=24000)
            sd.wait()


class KyutaiSpeaker:
    def __init__(self, voice="cml-tts/fr/10087_11650_000028-0002.wav", quantize_bits=8):
        import sentencepiece
        from moshi_mlx import models
        from moshi_mlx.models.tts import TTSModel, DEFAULT_DSM_TTS_REPO, DEFAULT_DSM_TTS_VOICE_REPO
        from moshi_mlx.utils.loaders import hf_get

        print(f"🎙️  Initialisation de Kyutai TTS 1.6B ({quantize_bits}-bit MLX)...")
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
            print(f"⚡ Quantification MLX en {quantize_bits}-bit...")
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
        print("✅ Kyutai TTS q8 prêt !\n")

    def set_speed(self, new_speed):
        self.speed = max(0.5, min(1.6, round(new_speed, 2)))

    def speak(self, text):
        clean_text = text.replace('"', '\\"').replace("\n", " ").strip()
        if not clean_text:
            return

        all_entries = [self.tts_model.prepare_script([clean_text])]
        voices = [self.tts_model.get_voice_path(self.voice)] if self.tts_model.multi_speaker else []
        all_attributes = [self.tts_model.make_condition_attributes(voices, self.cfg_coef_conditioning)]

        print("🔊 Synthèse vocale Kyutai...")
        res = self.tts_model.generate(all_entries, all_attributes, cfg_is_no_prefix=False, cfg_is_no_text=False)
        frames = mx.concat(res.frames, axis=-1)
        pcm = self.tts_model.mimi.decode(frames)
        arr = np.array(mx.clip(pcm[0, 0], -1, 1))

        sd.play(arr, samplerate=self.tts_model.mimi.sample_rate)
        sd.wait()


def build_prompt(conversation_history, tokenizer, is_qwen=True):
    if is_qwen:
        messages = [{"role": "system", "content": SYSTEM_PROMPT}] + conversation_history
        return tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
    else:
        # Mistral format
        formatted = "<s>"
        has_system_injected = False
        for msg in conversation_history:
            role = msg["role"]
            content = msg["content"]
            if role == "user":
                if not has_system_injected:
                    formatted += f"[INST] {SYSTEM_PROMPT}\n\nÉlève: {content} [/INST]"
                    has_system_injected = True
                else:
                    formatted += f"[INST] {content} [/INST]"
            elif role == "assistant":
                formatted += f" {content} </s>"
        return formatted


def manage_context_window(conversation, tokenizer, is_qwen=True, max_tokens=8000):
    while len(conversation) > 2:
        prompt = build_prompt(conversation, tokenizer, is_qwen=is_qwen)
        tokens = tokenizer.encode(prompt)
        if len(tokens) <= max_tokens:
            break
        conversation.pop(0)
        if conversation and conversation[0]["role"] == "assistant":
            conversation.pop(0)
    return conversation


def main():
    parser = argparse.ArgumentParser(description="French Voice Tutor with Apple MLX")
    parser.add_argument(
        "--tts",
        choices=["kokoro", "kyutai"],
        default="kokoro",
        help="Moteur vocal : 'kokoro' (instantané ~0.1s) ou 'kyutai' (Kyutai 1.6B q8 SOTA)",
    )
    parser.add_argument(
        "--model",
        default="qwen-3b",
        help="Modèle LLM : 'qwen-3b' (ultra-rapide ~48 tok/s), 'mistral' (7B), ou repo HF",
    )
    parser.add_argument("--voice", default="default", help="Voix TTS")
    parser.add_argument("--speed", type=float, default=0.92, help="Vitesse de parole (défaut: 0.92)")
    parser.add_argument("--max-context", type=int, default=8192, help="Taille max du contexte en tokens")
    parser.add_argument(
        "--whisper-model",
        default="mlx-community/whisper-base-mlx",
        help="Modèle MLX Whisper (défaut: whisper-base-mlx)",
    )
    args = parser.parse_args()

    model_repo = MODEL_ALIASES.get(args.model.lower(), args.model)
    is_qwen = "qwen" in model_repo.lower()

    print("\n" + "=" * 60)
    print("🇫🇷  TUTEUR DE FRANÇAIS (Apple Silicon MLX)")
    print("=" * 60)
    print(f"• LLM:      {model_repo} ({'⚡ Fast decode' if '3b' in model_repo.lower() else 'Standard'})")
    print(f"• Whisper:  {args.whisper_model}")
    print(f"• TTS:      {args.tts.upper()} ({'Kyutai 1.6B q8' if args.tts == 'kyutai' else 'Kokoro-82M'})")
    print(f"• Contexte: {args.max_context:,} tokens (~8K)")
    print("=" * 60 + "\n")

    print(f"🧠 Chargement du modèle {model_repo.split('/')[-1]}...")
    model, tokenizer = load(model_repo)
    print("✅ Modèle LLM prêt !\n")

    if args.tts == "kyutai":
        kyutai_voice = "cml-tts/fr/10087_11650_000028-0002.wav" if args.voice == "default" else args.voice
        speaker = KyutaiSpeaker(voice=kyutai_voice, quantize_bits=8)
    else:
        kokoro_voice = "ff_siwis" if args.voice == "default" else args.voice
        speaker = KokoroSpeaker(voice=kokoro_voice, speed=args.speed)

    recorder = AudioRecorder(sample_rate=16000)
    conversation = []

    greeting = "Bonjour ! Comment vas-tu aujourd'hui ?"
    print(f"🇫🇷 Tuteur: {greeting}\n")
    conversation.append({"role": "assistant", "content": greeting})
    speaker.speak(greeting)

    print("💡 Commandes disponibles à tout moment :")
    print("   • [Entrée]    : Parler (puis Entrée pour terminer)")
    print("   • [+] ou [-]  : Accélérer ou ralentir la voix")
    print("   • [r]         : Répéter la dernière phrase")
    print("   • [tokens]    : Voir les statistiques de contexte")
    print("   • Ctrl+C      : Quitter\n")

    while True:
        try:
            cmd = input(f"👉 [Entrée] Parler | [+] Plus vite | [-] Plus lent | [r] Répéter ({speaker.speed:.2f}x) : ").strip().lower()

            if cmd == "+":
                speaker.set_speed(speaker.speed + 0.05)
                print(f"⚡ Vitesse augmentée à {speaker.speed:.2f}x\n")
                continue
            elif cmd == "-":
                speaker.set_speed(speaker.speed - 0.05)
                print(f"🐢 Vitesse ralentie à {speaker.speed:.2f}x\n")
                continue
            elif cmd in ["r", "repeat", "repeter", "replay"]:
                last_reply = conversation[-1]["content"] if conversation else greeting
                print(f"🔁 Répétition : \"{last_reply}\"")
                speaker.speak(last_reply)
                print()
                continue
            elif cmd in ["tokens", "stats", "status"]:
                prompt_now = build_prompt(conversation, tokenizer, is_qwen=is_qwen)
                tok_count = len(tokenizer.encode(prompt_now))
                turns = len(conversation) // 2
                print(f"📊 Mémoire: {tok_count:,} / {args.max_context:,} tokens utilisés ({turns} tours)\n")
                continue
            elif cmd:
                try:
                    val = float(cmd.replace(",", "."))
                    speaker.set_speed(val)
                    print(f"🎯 Vitesse réglée à {speaker.speed:.2f}x\n")
                    continue
                except ValueError:
                    pass

            recorder.start()
            input("🎤  [Enregistrement en cours...] Appuie sur [Entrée] pour terminer.")
            audio = recorder.stop()

            duration = len(audio) / 16000.0
            if duration < 0.5 or np.max(np.abs(audio)) < 0.01:
                print("⚠️  Enregistrement trop court ou silencieux. Réessaie !")
                continue

            print("⚡ Transcription (Whisper GPU)...")
            result = mlx_whisper.transcribe(
                audio,
                path_or_hf_repo=args.whisper_model,
                language="fr",
            )
            user_text = result.get("text", "").strip()

            if not user_text:
                print("⚠️  Rien compris. Réessaie !")
                continue

            print(f"\n👤 Toi:    {user_text}")
            conversation.append({"role": "user", "content": user_text})

            effective_budget = max(512, args.max_context - 150)
            conversation = manage_context_window(conversation, tokenizer, is_qwen=is_qwen, max_tokens=effective_budget)

            prompt = build_prompt(conversation, tokenizer, is_qwen=is_qwen)

            print("🤖 Génération de la réponse...")
            t_gen0 = time.time()
            reply = generate(
                model,
                tokenizer,
                prompt=prompt,
                max_tokens=90,
                verbose=False,
            ).strip()

            for stop_token in ["</s>", "<|im_end|>", "<|endoftext|>"]:
                if stop_token in reply:
                    reply = reply.split(stop_token)[0].strip()

            gen_dur = time.time() - t_gen0
            tok_gen = len(tokenizer.encode(reply))
            print(f"🇫🇷 Tuteur ({tok_gen} tokens en {gen_dur:.2f}s, {tok_gen/max(0.01, gen_dur):.1f} tok/s): {reply}\n")
            conversation.append({"role": "assistant", "content": reply})

            speaker.speak(reply)

        except KeyboardInterrupt:
            print("\n\nAu revoir et à bientôt ! 👋")
            break


if __name__ == "__main__":
    main()
