# Écho (*Parle*) — Minimalist Local Voice Tutor Spec
**Target Platform:** macOS Apple Silicon (16GB Unified Memory)  
**Stack:** Tauri v2 (Rust) + Svelte 5 / Tailwind + Web Audio Canvas + Apple MLX + macOS Native Speech  
**Primary Goal:** A zero-friction, distraction-free French conversational partner tailored to inattentive ADHD. It replaces clunky AI dashboards with a two-pane interface: a **reactive sonic visualizer/mic on the left**, and a **live bilingual safety-net transcript on the right**.

---

## 1. The Core Vision & ADHD Principles

### The Problem with Existing Tools
* **AI Dashboards (Open WebUI, ChatGPT web):** Cluttered with sidebars, model dropdowns, system prompt fields, and chat histories. Creates activation friction and decision fatigue before you ever speak.
* **Pure Audio Tools:** Lacks visual anchoring. When you miss a fast French word or liaison, your brain loses the thread and zones out.
* **Heavy Frameworks (Electron):** Spawns a full Chromium instance consuming 250MB–450MB of RAM. On a 16GB Mac running a local 7B model (~4.5 GB), memory waste creates system thermal throttling and fan noise.

### The Écho Philosophy
1. **Zero Setup to Start:** Summon with a global hotkey (`Cmd + Shift + Space`) like Spotlight or Raycast. Talk, listen, dismiss.
2. **Dual-Modal Anchoring:** The visualizer keeps focus while speaking/listening; the real-time transcript acts as an instant comprehension safety net.
3. **No Memory Leaks (Clean Lifecycle):** The Rust backend acts as a supervisor. Quitting the app (`Cmd + Q`) instantly terminates all background MLX/Whisper processes, immediately restoring 100% of your RAM to macOS.

---

## 2. Visual Layout & Wireframe

```
┌──────────────────────────────────────────┬──────────────────────────────────────────┐
│             AUDIO ORB / MIC              │            LIVE TRANSCRIPTION            │
│                                          │                                          │
│                                          │  [14:02:10] 👤 Toi:                     │
│                                          │  "Hier, je suis allé au marché et j'ai   │
│                                          │   acheté des pommes."                    │
│                                          │                                          │
│                 ╭──────╮                 │  [14:02:12] 🇫🇷 Tuteur (Thomas):         │
│               │  ◉ ◉  │                │  "Très bien ! Qu'est-ce que tu vas       │
│              │   ~~~~   │               │   préparer avec ces pommes ?"            │
│               ╰──────╯                 │                                          │
│             [ PUSH TO TALK ]             │  [14:02:25] 👤 Toi:                     │
│             (Hold Spacebar ␣)            │  "Je veux faire une tarte..."            │
│                                          │                                          │
│  State: [ LISTENING / ANALYZING / SPEAKING ]                                        │
│  Latency: ~180ms  |  RAM: 4.8 GB (MLX)   │  [Hold Shift: Instant Translation]       │
└──────────────────────────────────────────┴──────────────────────────────────────────┘
```

### Left Pane: The Sonic Engine (Visualizer & Control)
* **The Dynamic Orb:**
  * **Idle:** Gentle, low-contrast breathing pulse (confirms model is ready without distracting).
  * **User Speaking (`LISTENING`):** Real-time frequency visualizer (reactive fluid particles or concentric ripples) tracking mic volume and pitch via Web Audio FFT.
  * **Thinking (`THINKING`):** Subtle orbital shimmer while local Whisper + Mistral generate tokens.
  * **Tutor Speaking (`SPEAKING`):** Harmonic oscillation synchronized with macOS voice playback.
* **Control Mechanism:**
  * **Push-to-Talk (Hold Spacebar):** Eliminates awkward cutoff timers or false voice triggers.
  * **Speed Dial (0.75x — 1.25x):** One click slider to slow down French output for challenging auditory processing days.

### Right Pane: The Cognitive Safety Net (Live Transcript)
* **Real-time Dual Transcript:** User's transcribed French on top, Tutor's reply directly underneath.
* **The "Panic / Translate" Key (Hold `Shift`):** Instantly overlays English translation above the French text. Release `Shift` to hide. Prevents giving up when you get lost.
* **Click-to-Replay:** Hover over any tutor bubble and click 🔊 to replay the sentence at 0.8x speed.
* **Vocabulary Chip Highlighting:** Gentle green underlines under newly introduced A2/B1 idioms.

---

## 3. System Architecture

```
┌────────────────────────────────────────────────────────────────────────┐
│                        TAURI v2 DESKTOP APP                            │
│                                                                        │
│  ┌─────────────────────────────────┐  ┌─────────────────────────────┐  │
│  │   Frontend (WebKit / HTML5)     │  │     Rust Backend (Tauri)    │  │
│  │  • Svelte 5 / React + Tailwind  │  │  • Window management        │  │
│  │  • Canvas / Three.js Visualizer │◄─┼─► • Global hotkey listener  │  │
│  │  • Live Transcript Feed        │  │  • Sidecar process manager  │  │
│  │  • Web Audio API (Mic/Speaker)  │  │  • macOS `say` IPC bridge   │  │
│  └─────────────────────────────────┘  └──────────────┬──────────────┘  │
└──────────────────────────────────────────────────────┼─────────────────┘
                                                       │ IPC / HTTP / Subprocess
                                                       ▼
                                        ┌─────────────────────────────┐
                                        │     Apple MLX Engine        │
                                        │  • `mlx-whisper` (STT)      │
                                        │  • Mistral 7B 4-bit (LLM)   │
                                        └─────────────────────────────┘
```

### Why Tauri v2 over Electron on a 16GB Mac

| Metric | Electron | Tauri v2 | Advantage for 16GB Mac |
| :--- | :--- | :--- | :--- |
| **Idle Memory (RAM)** | ~250MB – 450MB | **~30MB – 50MB** | Leaves all available RAM for Mistral 7B (~4.5 GB) |
| **Engine** | Bundled Chromium | macOS Native **WKWebView** | Hardware-accelerated Metal rendering built into OS |
| **App Bundle Size** | ~150MB+ | **~12MB – 18MB** | Instant cold-start launch time |
| **OS Integration** | Heavy Node.js native addons | Native **Rust crates** | Flawless global hotkeys, floating panels, window blur |

---

## 4. Audio & Inference Pipeline

```
1. Press Spacebar   ──► Mic Stream via Web Audio API ──► Canvas Visualizer reacts
2. Release Spacebar ──► Audio blob sent to mlx-whisper (Metal GPU, ~150ms)
3. Text output      ──► Displayed in Right Transcript pane
4. Prompt routed    ──► Local MLX Mistral 7B (Token streaming)
5. Text response    ──► Rendered token-by-token
6. Audio Playback   ──► macOS `say -v Thomas -r 150` via Tauri Rust IPC
```

### 1. Speech-to-Text (STT): `mlx-whisper`
* Uses `mlx-community/whisper-base-mlx` running natively on Apple Silicon.
* Processes French speech in ~100–180ms with 0 cloud latency.

### 2. Language Model (LLM): `mlx-community/Mistral-7B-Instruct-v0.3-4bit`
* Native 4-bit quantization takes **~4.3 GB Unified Memory**.
* Mistral was trained in France; its handling of natural French rhythm, colloquialisms, and pedagogy vastly outperforms competing 7B models.
* Served locally via `mlx_lm.server` or called directly via in-process Python worker.

### 3. Voice Synthesis (TTS): macOS Native Speech (`say`)
* Uses Apple's built-in French neural voices: `Thomas` (France) or `Amélie` (Canada).
* **0 MB additional VRAM required.**
* Zero synthesis latency.

---

## 5. Technical Stack Details

* **Desktop Shell:** Tauri v2 (`@tauri-apps/cli`)
* **Window Styling:** Frameless, floating HUD with macOS vibrancy/blur (`NSVisualEffectView`)
* **Frontend Framework:** Svelte 5 (or React with Vite) + Tailwind CSS
* **Audio Visualizer:** HTML5 2D Canvas or WebGL fragment shader driven by `AnalyserNode.getByteFrequencyData()`
* **Shortcuts:** `tauri-plugin-global-shortcut` (Summon HUD: `Cmd+Shift+Space`)
* **Local Backend:** Python 3.12 virtualenv running `mlx`, `mlx-lm`, and `mlx-whisper`

---

## 6. Phased Implementation Plan

### Phase 1: Local Backend & Audio Validation (No UI)
- [x] Configure Python 3.12 environment in `.venv`.
- [x] Install `mlx`, `mlx-lm`, `mlx-whisper`, `sounddevice`.
- [x] Verify audio input / output and macOS native `say` voices (`Thomas`, `Amélie`).
- [ ] Spin up `mlx_lm.server` with `Mistral-7B-Instruct-v0.3-4bit` at `localhost:8080`.

### Phase 2: Tauri Project Scaffolding
- [ ] Initialize Tauri v2 project (`npm create tauri-app@latest`).
- [ ] Configure `tauri.conf.json` for frameless, floating window with transparency.
- [ ] Set macOS microphone permissions (`NSMicrophoneUsageDescription`).
- [ ] Implement Rust Tauri command for macOS speech synthesis:
  ```rust
  #[tauri::command]
  fn speak(text: String, voice: String, rate: u32) {
      std::process::Command::new("say")
          .args(["-v", &voice, "-r", &rate.to_string(), &text])
          .spawn()
          .ok();
  }
  ```

### Phase 3: Frontend Visualizer & Split View
- [ ] Build 2-column layout (Visualizer left, Transcript right).
- [ ] Implement Web Audio `AnalyserNode` connected to microphone stream.
- [ ] Render 60 FPS reactive orb on `<canvas>` (idle / listening / thinking / speaking states).
- [ ] Add Spacebar push-to-talk handler.

### Phase 4: Full Loop Integration & ADHD Polish
- [ ] Connect audio release to `mlx-whisper` STT and stream response from `mlx_lm`.
- [ ] Hook output text to Rust `speak` command and mirror frequencies back to the orb.
- [ ] Add Shift-key instantaneous English translation reveal.
- [ ] Implement auto-shutdown: Rust kills the MLX background process on app exit (`Cmd + Q`).
