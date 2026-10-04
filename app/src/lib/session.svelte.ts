// App state, fed by engine events. Svelte 5 runes in a class.

import { getBridge, type Bridge } from "./bridge";
import { strings } from "./i18n";
import type {
  EngineConfig,
  EngineEvent,
  EngineExit,
  EngineState,
  Settings,
  Turn,
  TurnMode,
  TurnStats,
  PromptName,
} from "./types";

export type Phase = "boot" | "setup" | "loading" | "ready" | "crashed";
export type View = "chat" | "prompts" | "settings";

/** Audio levels change ~30x per second; the orb reads them directly, so
 * they live outside the reactive state. */
export const levels = {
  mic: { rms: 0, bands: new Array(8).fill(0) as number[], at: 0 },
  out: { rms: 0, bands: new Array(8).fill(0) as number[], at: 0 },
};

/** Peak memory measured on an M4 Pro (VOICE_APP_SPEC.md §4.7). Same table as
 * memory_estimate_gb() in serve.py. */
export function memoryEstimate(s: Pick<Settings, "stt" | "turn" | "tts">): number {
  const kyutaiStt = s.stt === "kyutai" || s.turn === "semantic";
  if (s.tts === "kyutai") return kyutaiStt ? 11.2 : 10.0;
  if (kyutaiStt) return 7.2;
  if (s.stt === "whisper") return 6.1;
  return 5.9;
}

export class Session {
  /** GPU memory macOS recommends on this Mac (from the engine), or null. */
  get budgetGb(): number | null {
    return this.config?.memory?.recommended_gb ?? null;
  }

  fits(s: Pick<Settings, "stt" | "turn" | "tts">): boolean {
    const b = this.budgetGb;
    return b === null || memoryEstimate(s) <= b;
  }

  phase = $state<Phase>("boot");
  view = $state<View>("chat");
  promptsReloadedAt = $state(0);
  /** English prompt -> French translation in progress or just finished. */
  promptTranslation = $state<{ req: number; name: PromptName; text: string; done: boolean; missing?: string[] } | null>(null);
  #promptReq = 0;
  state = $state<EngineState>("loading");
  mic = $state(false);
  paused = $state(false);
  loadingStage = $state("imports");
  setupStage = $state<string | null>(null);
  setupLines = $state<string[]>([]);
  downloads = $state<Record<string, { done: number; total: number | null; finished?: boolean }>>({});
  config = $state<EngineConfig | null>(null);
  settings = $state<Settings | null>(null);
  turns = $state<Turn[]>([]);
  partial = $state("");
  stats = $state<TurnStats | null>(null);
  speed = $state(0.92);
  mode = $state<TurnMode>("ptt");
  notice = $state<{ text: string; at: number } | null>(null);
  warnings = $state<string[]>([]);
  crash = $state<EngineExit | null>(null);
  listeningSince = $state<number | null>(null);
  maxAudio = $state(30);
  translating = $state(false);
  speakingId = $state<number | null>(null);

  bridge!: Bridge;
  #requested = new Set<string>();
  #lastAutoRestart = 0;
  #noticeTimer: ReturnType<typeof setTimeout> | undefined;

  get t() {
    return strings[this.settings?.ui_lang ?? "fr"];
  }

  async init() {
    this.bridge = await getBridge();
    this.bridge.onEngine((e) => this.#onEngine(e));
    this.bridge.onExit((e) => {
      // One automatic restart per minute for a crash after a good start.
      // Setup failures and fatal load errors (e.g. missing moshi_mlx) would
      // only fail again, so they wait for the learner.
      const now = Date.now();
      if (this.phase === "ready" && !e.setup && now - this.#lastAutoRestart > 60_000) {
        this.#lastAutoRestart = now;
        this.showNotice(this.t.crashTitle);
        void this.restart();
        return;
      }
      if (this.phase === "crashed" && this.crash) {
        // Keep the fatal message on top, add the log tail below it.
        this.crash = { ...e, tail: `${this.crash.tail}\n\n${e.tail}` };
        return;
      }
      this.crash = e;
      this.phase = "crashed";
      this.state = "loading";
    });
    this.bridge.onSetup((e) => {
      if (this.phase === "boot" || this.phase === "crashed") this.phase = "setup";
      this.setupStage = e.stage;
      if (e.line) this.setupLines = [...this.setupLines.slice(-200), e.line];
    });
    this.settings = await this.bridge.getSettings();
    this.speed = this.settings.speed;
    this.mode = this.settings.turn;
    await this.bridge.start();
  }

  async restart() {
    this.crash = null;
    this.phase = "boot";
    this.state = "loading";
    this.config = null;
    this.downloads = {};
    this.setupLines = [];
    this.turns = [];
    this.#requested.clear();
    await this.bridge.restart();
  }

  send(msg: Record<string, unknown>) {
    return this.bridge.send(msg).catch((e) => console.warn("engine_send", e));
  }

  showNotice(text: string) {
    this.notice = { text, at: Date.now() };
    clearTimeout(this.#noticeTimer);
    this.#noticeTimer = setTimeout(() => (this.notice = null), 3500);
  }

  #turn(id: number): Turn | undefined {
    return this.turns.find((t) => t.id === id);
  }

  #onEngine(e: EngineEvent) {
    switch (e.event) {
      case "hello":
        if (this.phase !== "ready") this.phase = "loading";
        break;
      case "loading":
        this.loadingStage = e.stage;
        if (this.phase === "boot" || this.phase === "setup") this.phase = "loading";
        break;
      case "download":
        this.downloads = { ...this.downloads, [e.repo]: { done: e.done, total: e.total, finished: e.finished } };
        break;
      case "warning":
        if (!this.warnings.includes(e.message)) this.warnings = [...this.warnings, e.message];
        break;
      case "ready":
        this.config = e.config;
        this.speed = e.config.speed;
        this.mode = e.config.turn;
        this.maxAudio = e.config.max_audio_s;
        this.phase = "ready";
        break;
      case "state":
        if (e.state === "listening" && this.state !== "listening") {
          this.listeningSince = performance.now();
          this.partial = "";
        }
        if (e.state !== "listening") this.listeningSince = null;
        if (e.max_s) this.maxAudio = e.max_s;
        this.speakingId = e.state === "speaking" ? (e.id ?? null) : null;
        this.state = e.state;
        this.mic = e.mic;
        break;
      case "level": {
        const l = levels[e.src];
        l.rms = e.rms;
        l.bands = e.bands;
        l.at = performance.now();
        break;
      }
      case "partial":
        this.partial = e.text;
        break;
      case "notice":
        this.showNotice(this.t.notices[e.key] ?? e.key);
        break;
      case "user_turn":
        this.partial = "";
        this.turns.push({
          id: e.id,
          at: new Date(),
          transcript: e.transcript,
          pronunciation: e.pronunciation,
          reponse: "",
          streaming: true,
        });
        break;
      case "reply_delta": {
        const t = this.#turn(e.id);
        if (t) {
          t.correction = e.correction || null;
          t.reponse = e.reponse;
        }
        break;
      }
      case "reply_done": {
        let t = this.#turn(e.id);
        if (!t) {
          t = { id: e.id, at: new Date(), reponse: "", streaming: false, greeting: e.greeting };
          this.turns.push(t);
          t = this.#turn(e.id)!;
        }
        t.correction = e.correction;
        t.reponse = e.reponse;
        t.streaming = false;
        if (this.translating) this.requestTranslations();
        break;
      }
      case "turn_stats":
        this.stats = e;
        break;
      case "translation": {
        const t = this.#turn(e.id);
        if (t) t.translation = { transcript: e.transcript, reponse: e.reponse };
        break;
      }
      case "vocab": {
        const t = this.#turn(e.id);
        if (t) t.vocab = e.items;
        break;
      }
      case "speed":
        this.speed = e.speed;
        break;
      case "mode":
        this.mode = e.mode;
        break;
      case "prompt_translation":
        if (this.promptTranslation && e.req === this.promptTranslation.req) {
          this.promptTranslation = { ...this.promptTranslation, text: e.text, done: e.done, missing: e.missing };
        }
        break;
      case "prompts_reloaded":
        this.promptsReloadedAt = Date.now();
        this.showNotice(this.t.prompts.reloaded);
        break;
      case "error":
        this.showNotice(e.message);
        break;
      case "fatal":
        this.crash = { code: 1, tail: e.message, setup: false };
        this.phase = "crashed";
        break;
    }
  }

  // -- actions ---------------------------------------------------------------

  pttStart() {
    if (this.phase !== "ready" || this.mode !== "ptt") return;
    this.send({ cmd: "ptt_start" });
  }

  pttStop() {
    if (this.mode !== "ptt") return;
    this.send({ cmd: "ptt_stop" });
  }

  /** Ask Gemma to translate an English prompt into French (streamed). */
  translatePrompt(name: PromptName, text: string) {
    const req = ++this.#promptReq;
    this.promptTranslation = { req, name, text: "", done: false };
    this.send({ cmd: "translate_prompt", name, text, req });
  }

  stopVoice() {
    this.send({ cmd: "stop" });
  }

  replay(id: number, slow: boolean) {
    this.send(slow ? { cmd: "replay", id, speed: 0.8 } : { cmd: "replay", id });
  }

  /** Ask for the translation of the most recent turns that lack one. */
  requestTranslations() {
    for (const t of this.turns.slice(-6)) {
      if (t.translation || t.streaming) continue;
      const key = `tr:${t.id}`;
      if (this.#requested.has(key)) continue;
      this.#requested.add(key);
      this.send({ cmd: "translate", id: t.id });
    }
  }

  setTranslating(on: boolean) {
    this.translating = on;
    if (on) this.requestTranslations();
  }

  #saveTimer: ReturnType<typeof setTimeout> | undefined;

  setSpeed(speed: number) {
    this.speed = speed;
    this.send({ cmd: "set_speed", speed });
    if (this.settings) {
      this.settings.speed = speed;
      clearTimeout(this.#saveTimer);
      const s = $state.snapshot(this.settings);
      this.#saveTimer = setTimeout(() => this.bridge.saveSettings(s), 400);
    }
  }

  /** Save settings; apply live what can be applied, restart otherwise. */
  async applySettings(next: Settings): Promise<void> {
    const prevTurn = this.settings?.turn;
    const restart = await this.bridge.saveSettings(next);
    this.settings = await this.bridge.getSettings();
    if (restart) {
      await this.restart();
      return;
    }
    if (this.settings.turn !== prevTurn) this.send({ cmd: "set_turn_mode", mode: this.settings.turn });
  }
}

export const session = new Session();
