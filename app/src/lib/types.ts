// Protocol types shared with serve.py (see the docstring there).

export type EngineState = "loading" | "idle" | "listening" | "hearing" | "thinking" | "speaking";
export type TurnMode = "ptt" | "vad" | "semantic";
export type Stt = "audio" | "whisper" | "kyutai";
export type Tts = "kokoro" | "kyutai";

export interface MemoryInfo {
  device: string | null;
  total_gb: number | null;
  /** Metal's recommendedMaxWorkingSetSize: the system's guidance for GPU memory. */
  recommended_gb: number | null;
  estimate_gb: number;
}

export interface EngineConfig {
  memory: MemoryInfo;
  model: string;
  stt: Stt;
  turn: TurnMode;
  tts: Tts;
  voice: string;
  speed: number;
  max_context: number;
  max_audio_s: number;
  output_device: string;
  headphones_likely: boolean;
  prompts: { tutor: string; hear: string };
}

export interface Settings {
  stt: Stt;
  turn: TurnMode;
  tts: Tts;
  speed: number;
  silence: number;
  ui_lang: "fr" | "en";
  prefetch: boolean;
  greet: boolean;
}

export interface VocabItem {
  fr: string;
  lemma: string;
  en: string;
}

export interface Translation {
  transcript?: string | null;
  reponse?: string | null;
}

export interface Turn {
  id: number;
  at: Date;
  greeting?: boolean;
  transcript?: string;
  pronunciation?: string | null;
  correction?: string | null;
  reponse: string;
  streaming: boolean;
  translation?: Translation;
  vocab?: VocabItem[];
}

export interface TurnStats {
  id: number;
  hear_s: number;
  ttft_s: number;
  first_audio_s: number | null;
  tok_s: number;
  peak_gb: number;
  cached_tokens?: number;
  prompt_tokens?: number;
  interrupted?: boolean;
}

export type EngineEvent =
  | { event: "hello"; pid: number }
  | { event: "loading"; stage: string }
  | { event: "download"; repo: string; done: number; total: number | null; finished?: boolean }
  | { event: "warning"; key?: string; message: string; need_gb?: number; budget_gb?: number }
  | { event: "ready"; config: EngineConfig }
  | { event: "state"; state: EngineState; mic: boolean; max_s?: number; id?: number; replay?: boolean }
  | { event: "level"; src: "mic" | "out"; rms: number; bands: number[] }
  | { event: "partial"; text: string }
  | { event: "notice"; key: string; seconds?: number }
  | { event: "user_turn"; id: number; transcript: string; pronunciation: string | null }
  | { event: "reply_delta"; id: number; correction: string; reponse: string }
  | { event: "reply_done"; id: number; correction: string | null; reponse: string; greeting?: boolean }
  | ({ event: "turn_stats" } & TurnStats)
  | ({ event: "translation"; id: number } & Translation)
  | { event: "vocab"; id: number; items: VocabItem[] }
  | { event: "stats"; context_tokens: number; max_context: number; turns: number; speed: number }
  | { event: "speed"; speed: number }
  | { event: "mode"; mode: TurnMode }
  | { event: "error"; message: string }
  | { event: "fatal"; message: string }
  | { event: "bye" };

export interface EngineExit {
  code: number | null;
  tail: string;
  setup?: boolean;
}

export interface SetupEvent {
  stage: string;
  line: string | null;
}
