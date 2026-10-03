# Bavard

A local French voice conversation loop running entirely on Apple Silicon via MLX.

Designed for spoken language practice without cloud latency, subscription fees, or awkward voice-mode cutoffs. Everything runs locally in unified memory on a 16GB Mac.

## How it works

The pipeline consists of three offline models:

1. **STT:** `mlx-whisper` (`base` by default) transcribes speech on the Apple Neural Engine/GPU.
2. **LLM:** `Qwen2.5-3B-Instruct` (4-bit) generates concise French replies and gentle grammar corrections at ~48 tokens/sec. Mistral 7B is also supported.
3. **TTS:** `Kokoro-82M` synthesizes natural 24kHz French audio in ~150ms. Kyutai TTS 1.6B (quantized to 8-bit) is available as an alternative.

Total RAM usage sits under 6.5 GB, leaving ample headroom on a 16GB machine. Quitting the script immediately releases all memory back to the OS.

## Requirements

- Apple Silicon Mac (M1/M2/M3/M4)
- macOS 14+
- Python 3.12+

## Usage

Clone the repo and run the startup script:

```bash
git clone https://github.com/aureleon/bavard.git
cd bavard
chmod +x run.sh
./run.sh
```

Dependencies and model weights download automatically on first run.

### Controls

During a session:

- **Enter**: Start talking, then press Enter again when finished (push-to-talk).
- **`+` / `-`**: Adjust speech rate by ±0.05x on the fly.
- **`r`**: Replay the tutor's last sentence.
- **`0.8`** (or any number): Jump to a specific playback speed.
- **`stats`**: Check context token usage.
- **Ctrl+C**: Exit.

### CLI Flags

```bash
# Default: Qwen 2.5 3B (~48 tok/s) + Kokoro TTS
./run.sh

# Use Kyutai TTS 1.6B (8-bit)
./run.sh --tts kyutai

# Use Mistral 7B (slower decode, ~21 tok/s)
./run.sh --model mistral

# Set initial playback speed (default is 0.92)
./run.sh --speed 0.85

# Use Whisper Small for higher transcription accuracy
./run.sh --whisper-model mlx-community/whisper-small-mlx
```

## License

MIT
