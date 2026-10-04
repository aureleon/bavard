# Bavard

A local French voice conversation loop running entirely on Apple Silicon via MLX.

Designed for spoken language practice without cloud latency, subscription fees, or awkward voice-mode cutoffs. Everything runs locally in unified memory on a 16GB Mac.

## How it works

Each turn has four stages:

1. **Capture:** three turn modes.
   - `--turn ptt` (default): push-to-talk. Press Enter to start and to stop.
   - `--turn vad`: hands-free. Silero VAD ends the turn after 1.2 s of silence (`--silence`).
   - `--turn semantic`: hands-free. Kyutai STT predicts the end of your sentence from what you said, usually ~0.5 s after you stop. A live transcript appears while you speak.
2. **Hear:** three methods.
   - `--stt audio` (default): **Gemma 4 E4B listens to your voice directly.** A short audio-only pass returns a transcript that **keeps the mistakes you can hear** ("à le", "je avoir") and uses standard spelling for anything that sounds the same. It also notes audible pronunciation problems.
   - `--stt whisper`: `mlx-whisper` makes the transcript. It is fast, but Whisper often fixes your mistakes silently.
   - `--stt kyutai`: Kyutai STT 1B (q8) **transcribes while you speak**, so the transcript is ready almost as soon as you stop. It keeps some mistakes, fixes others, and sometimes adds words ("je achète" → "je l'achète"). It has no prompt, so this cannot be tuned.
3. **Tutor:** `Gemma 4 E4B` (4-bit, via `mlx-vlm`) answers in a fixed format:
   ```
   CORRECTION: <corrected sentence + short explanation, or "RAS">   ← shown on screen
   RÉPONSE: <2–3 short sentences that end with a question>          ← spoken
   ```
   It corrects only mistakes you can **hear** (for example "à le" → "au", "un maison" → "une maison"). It ignores spelling that sounds the same ("des pomme rouge"). The prompts are in `prompts/` (see [Editing the prompts](#editing-the-prompts)).
4. **Speak:** `Kokoro-82M` speaks the `RÉPONSE` **while it is still being generated**. The first chunk is cut at the first comma, so audio starts after a few words. All audio goes to one continuous output stream, so chunks play back to back. Kyutai TTS 1.6B (q8, streamed frame by frame) is also available.

### Why the hear stage is separate

The audio pass runs without chat history. Only the text transcript goes into the history. The history stays text-only, so the KV prefix cache is reused on every turn and TTFT stays at ~0.1 s even with a long conversation. If audio were sent with the full history, the whole context would need a new prefill on every turn.

### Pipelines

The three choices are independent, so there are 18 combinations (3 inputs × 3 turn modes × 2 voices):

| Choice | Options |
|---|---|
| `--stt` | `audio` (default), `whisper`, `kyutai` |
| `--turn` | `ptt` (default), `vad`, `semantic` |
| `--tts` | `kokoro` (default), `kyutai` |

`--turn semantic` loads Kyutai STT even with `--stt audio` or `--stt whisper`. Kyutai then only decides when your turn ends and shows the live transcript. Gemma or Whisper still makes the transcript that the tutor reads. With `--stt audio`, this is the hybrid mode: fast turn-taking, and your mistakes are kept.

### Memory and speed

The context window is 16K tokens (`--max-context`, default 16384). Older turns are dropped when the context is full.

Peak memory measured on an M4 Pro:

| Setup | Peak memory |
|---|---|
| Gemma + Kokoro (+ Silero VAD) | ~5.9 GB |
| + Whisper (`--stt whisper`) | ~6.1 GB |
| + Kyutai STT q8 (`--stt kyutai` or `--turn semantic`) | ~7.2 GB (~7.9 GB in bf16) |
| Gemma + Kyutai TTS q8 | ~10.0 GB |
| Gemma + Kyutai STT q8 + Kyutai TTS q8 | ~11.2 GB |

For a 10 GB budget, do not combine Kyutai STT with Kyutai TTS.

Timings measured on an M4 Pro with Kokoro (expect about half the token speed on a base M4). "Turn end" is the time from the end of your speech until the turn closes. In push-to-talk, that is when you press Enter.

| Stage | `--stt audio` | `--stt whisper` | `--stt kyutai` |
|---|---|---|---|
| Hear, after the turn ends | ~0.8–0.9 s | ~0.07 s | ~0.1–0.7 s (flushes the last words) |
| Tutor TTFT (prefix cache hit) | ~0.06 s | ~0.13 s | ~0.13 s |
| Generation | ~65 tok/s | ~65 tok/s | ~65 tok/s |
| First audio, after the turn ends | ~1.5–1.9 s | ~0.8–1.0 s | ~1.0–1.7 s |

| Turn mode | Turn end after you stop talking |
|---|---|
| `ptt` | when you press Enter |
| `vad` | ~1.2 s (`--silence`) |
| `semantic` | ~0.2–0.5 s on a complete sentence |

The startup warm-up also fills the prefix cache, so the first turn is as fast as later ones. Each turn prints these timings. Type `stats` to see context use, cache hits, and peak memory.

## Requirements

- Apple Silicon Mac (M1/M2/M3/M4)
- macOS 14+
- Python 3.12+
- Headphones for `--turn vad` and `--turn semantic` (there is no echo cancellation; the mic is closed while the tutor speaks)

## Usage

Clone the repo and run the startup script:

```bash
git clone https://github.com/aureleon/bavard.git
cd bavard
chmod +x run.sh
./run.sh
```

Dependencies and model weights download automatically on first run. `run.sh` reinstalls dependencies when `requirements.txt` changes.

### Controls

Push-to-talk mode (`--turn ptt`):

- **Enter**: Start talking, then press Enter again when finished.
- **`+` / `-`**: Adjust speech rate by ±0.05x on the fly.
- **`0.8`** (or any number): Jump to a specific playback speed.
- **`r`**: Replay the tutor's last reply.
- **`stats`**: Show context, prefix-cache hits, speed, and peak memory.
- **`q`** or **Ctrl+C**: Exit.

Hands-free modes (`--turn vad`, `--turn semantic`): just talk. Press **Ctrl+C** to open the pause menu (same commands; **Enter** resumes, **`q`** quits).

### CLI flags

```bash
# Default: Gemma hears your voice + push-to-talk + Kokoro
./run.sh

# Whisper speech-to-text instead of Gemma audio input
./run.sh --stt whisper
./run.sh --stt whisper --whisper-model mlx-community/whisper-large-v3-turbo

# Kyutai STT, streaming (needs the Kyutai setup below; --kyutai-stt-bits 4, 8 or 0 = bf16)
./run.sh --stt kyutai

# Hands-free, end of turn after a silence (longer pause for slow speakers)
./run.sh --turn vad
./run.sh --turn vad --silence 1.8

# Hands-free, Kyutai detects the end of your sentence (hybrid: Gemma still transcribes)
./run.sh --turn semantic
./run.sh --turn semantic --eot-threshold 0.8   # higher = waits for more certainty

# Combine freely
./run.sh --stt kyutai --turn semantic
./run.sh --stt whisper --turn vad

# Other models: gemma-e2b, gemma-12b (audio input), qwen-3b, qwen-7b, mistral (text only → --stt whisper or kyutai)
./run.sh --model gemma-e2b
./run.sh --model qwen-3b --stt whisper

# Kyutai TTS 1.6B, 8-bit MLX quantization (default; --kyutai-bits 4 or 0 = bf16)
# Needs a manual install first, see "Kyutai setup" below
./run.sh --tts kyutai
./run.sh --tts kyutai --no-frame-streaming   # old behavior: play each chunk when complete

# Voice: a Kokoro voice name (default ff_siwis) or a Kyutai voice path
# (default cml-tts/fr/10087_11650_000028-0002.wav, from kyutai/tts-voices)
./run.sh --voice ff_siwis

# Custom system prompts (see "Editing the prompts")
./run.sh --tutor-prompt prompts/tutor-b2.txt

# Initial playback speed (default 0.92); tutor temperature (default 1.0 for Gemma)
./run.sh --speed 0.85 --temperature 0.7
```

### Editing the prompts

The system prompts are plain text files. Edit them directly, then restart Bavard:

| File | Used by | Purpose |
|---|---|---|
| `prompts/tutor.txt` | every turn | Tutor persona, level (A1/A2 by default), correction rules, reply format |
| `prompts/hear.txt` | `--stt audio` | How Gemma transcribes your voice (keep audible mistakes, standard spelling for silent ones) |

Keep the output markers, because the code parses them:

- `tutor.txt` must ask for `CORRECTION:` (shown on screen) and `RÉPONSE:` (spoken).
- `hear.txt` must ask for `TRANSCRIPTION:` and `PRONONCIATION:`.

Bavard warns at startup if a marker is missing. To keep several versions, pass a different file:

```bash
./run.sh --tutor-prompt prompts/tutor-b2.txt
./run.sh --hear-prompt ~/my-hear-prompt.txt
```

### Kyutai setup (TTS and STT)

Kyutai TTS (`--tts kyutai`), Kyutai STT (`--stt kyutai`) and `--turn semantic` all need `moshi_mlx`. It pins an old MLX version that conflicts with Gemma 4 support in `mlx-vlm`. For that reason it is not in `requirements.txt`. Install it without its pinned dependencies:

```bash
./.venv/bin/pip install --no-deps moshi_mlx rustymimi sphn
./.venv/bin/pip install aiohttp sentencepiece
```

Kyutai runs on MLX. MLX ties arrays to per-thread streams, so Bavard builds the Kyutai model on its own synthesis thread. It then synthesizes while Gemma is still generating, sharing the GPU.

Kyutai audio is also **streamed frame by frame**: each 80 ms Mimi frame is decoded and played as soon as the model makes it, after a 0.24 s jitter buffer. It does not wait for the whole chunk. Use `--no-frame-streaming` to compare.

Measured on an M4 Pro (q8, first audio after you stop talking):

| TTS | First audio | Gaps between sentences |
|---|---|---|
| Kokoro | ~1.9–2.1 s | none |
| Kyutai q8, frame streaming (default) | ~2.8 s | none |
| Kyutai q8, `--no-frame-streaming` | ~3.4–4.1 s | sometimes (~0.7 s) |

Kyutai has a built-in delay of about 1.3 s of generated audio before the first sound, so it stays behind Kokoro. Gemma slows from ~66 to ~53 tok/s while both models run at once.

Kyutai STT uses the `kyutai/stt-1b-en_fr-candle` weights, which include the end-of-turn head. It is quantized to q8 by default (`--kyutai-stt-bits`). On the test sentences, q8 gave the same transcripts as bf16 and used ~0.75 GB less. The text lags the audio by up to ~1.3 s, so at the end of a turn Bavard feeds it up to 1.5 s of silence to push out the last words. It stops early at the final punctuation.

Keep the default `--kyutai-bits 8`. In bf16 (`--kyutai-bits 0`), Kyutai is ~3× slower, which is slower than real time, so you will hear gaps between sentences.

## Limitations

- **The transcript is useful, not perfect.** Even when told to keep errors, Gemma sometimes fixes them silently.
- **Silent spelling can still be "corrected".** The tutor is told to correct only audible mistakes. But if the transcript itself has a spelling mistake that sounds the same (possible with `--stt whisper` or `--stt kyutai`), the tutor often corrects it anyway.
- **Tested with synthetic speech.** The timings and transcript checks use macOS TTS sentences, not real learner voices.
- **Pronunciation feedback is coarse.** Gemma notices clearly wrong words or vowels. It does not give phoneme-level scores.
- **`--turn semantic` can cut you off during long pauses.** In the tests, pauses of ~0.9 s in the middle of a sentence ("je voudrais… comment dire…") were taken as the end of the turn. Raising `--eot-threshold` does not help, because the model is very confident in those pauses. If this happens often, use `--turn vad` with a longer `--silence`.
- **Kyutai STT cannot be prompted.** It has no instructions input, so it cannot be told to keep your mistakes. Use `--stt audio` (alone or with `--turn semantic`) for error detection.
- **Audio turns are limited to 30 s** (the Gemma 4 audio encoder limit). Longer recordings are cut.
- `gemma-12b` audio input has not been tested.

## License

MIT
