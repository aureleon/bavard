// Scripted stand-in for serve.py, used only outside Tauri (browser dev).

import type { Bridge } from "./bridge";
import type { EngineEvent, Settings } from "./types";

const REPLIES = [
  {
    heard: "Hier, je suis allé à le marché et j'ai acheté un pomme.",
    correction: "Hier, je suis allé au marché et j'ai acheté une pomme. (à + le = au ; pomme est féminin)",
    reponse: "Très bien ! Tu as pris ton temps pour choisir ? Qu'est-ce que tu vas préparer avec cette pomme ?",
    vocab: [{ fr: "pris ton temps", lemma: "prendre son temps", en: "to take one's time" }],
    en: {
      transcript: "Yesterday, I went to the market and bought an apple.",
      reponse: "Very good! Did you take your time choosing? What are you going to make with this apple?",
    },
  },
  {
    heard: "Je veux faire une tarte avec ma grand-mère.",
    correction: null,
    reponse: "Quelle bonne idée ! Ça a l'air délicieux. Ta grand-mère a une recette secrète ?",
    vocab: [{ fr: "Ça a l'air", lemma: "avoir l'air", en: "to look / seem" }],
    en: {
      transcript: "I want to make a pie with my grandmother.",
      reponse: "What a good idea! That sounds delicious. Does your grandmother have a secret recipe?",
    },
  },
];

export function mockBridge(): Bridge {
  const engineCbs: ((e: EngineEvent) => void)[] = [];
  const emit = (e: EngineEvent) => engineCbs.forEach((cb) => cb(e));
  let settings: Settings = {
    stt: "audio",
    turn: "ptt",
    tts: "kokoro",
    speed: 0.92,
    silence: 1.2,
    ui_lang: "fr",
    prefetch: true,
    greet: true,
  };
  let state = "loading";
  let src: "mic" | "out" | null = null;
  let n = 0;
  let speed = 0.92;
  const turns: Record<number, (typeof REPLIES)[number]> = {};

  const setState = (s: EngineState) => {
    state = s;
    emit({ event: "state", state: s, mic: s === "listening", max_s: s === "listening" ? 30 : undefined });
  };
  type EngineState = Extract<EngineEvent, { event: "state" }>["state"];

  setInterval(() => {
    if (!src) return;
    const t = performance.now() / 1000;
    const bands = Array.from({ length: 8 }, (_, i) =>
      Math.max(0, 0.45 + 0.35 * Math.sin(t * (3 + i) + i) * Math.random()),
    );
    emit({ event: "level", src, rms: 0.4 + 0.3 * Math.abs(Math.sin(t * 5)), bands });
  }, 33);

  const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

  async function speak(ms: number) {
    setState("speaking");
    src = "out";
    await sleep(ms);
    src = null;
    setState("idle");
  }

  async function boot() {
    emit({ event: "hello", pid: 0 });
    for (const stage of ["imports", "download", "llm", "tts", "warmup"]) {
      emit({ event: "loading", stage });
      await sleep(250);
    }
    emit({
      event: "ready",
      config: {
        // A 16 GB Mac: Metal recommends ~11.5 GB for the GPU.
        memory: { device: "Apple M4", total_gb: 17.2, recommended_gb: 11.5, estimate_gb: 5.9 },
        model: "mlx-community/gemma-4-e4b-it-4bit",
        stt: settings.stt,
        turn: settings.turn,
        tts: settings.tts,
        voice: "ff_siwis",
        speed,
        max_context: 16384,
        max_audio_s: 30,
        output_device: "AirPods",
        headphones_likely: true,
        prompts: { tutor: "prompts/tutor.txt", hear: "prompts/hear.txt" },
      },
    });
    emit({ event: "reply_done", id: 0, correction: null, reponse: "Bonjour ! Comment vas-tu aujourd'hui ?", greeting: true });
    await speak(1500);
  }

  async function turn() {
    const r = REPLIES[n % REPLIES.length];
    const id = ++n;
    turns[id] = r;
    setState("hearing");
    await sleep(800);
    emit({ event: "user_turn", id, transcript: r.heard, pronunciation: null });
    setState("thinking");
    const raw = `CORRECTION: ${r.correction ?? "RAS"}\nRÉPONSE: ${r.reponse}`;
    for (let i = 1; i <= raw.length; i += 4) {
      const text = raw.slice(0, i);
      const m = text.match(/R[ÉE]PONSE\s*:\s*/);
      const corr = (m ? text.slice(0, m.index) : text).replace(/^\s*CORRECTION\s*:\s*/, "").trim();
      emit({
        event: "reply_delta",
        id,
        correction: "RAS".startsWith(corr.toUpperCase()) ? "" : corr,
        reponse: m ? text.slice(m.index! + m[0].length) : "",
      });
      if (i === 40) {
        setState("speaking");
        src = "out";
      }
      await sleep(15);
    }
    emit({ event: "reply_done", id, correction: r.correction, reponse: r.reponse });
    emit({ event: "vocab", id, items: r.vocab });
    await speak(2500);
    emit({
      event: "turn_stats",
      id,
      hear_s: 0.82,
      ttft_s: 0.07,
      first_audio_s: 1.62,
      tok_s: 66,
      peak_gb: 5.9,
    });
  }

  return {
    getSettings: async () => settings,
    saveSettings: async (s) => {
      const restart = s.stt !== settings.stt || s.tts !== settings.tts;
      settings = s;
      return restart;
    },
    start: async () => void boot(),
    restart: async () => void boot(),
    send: async (msg) => {
      switch (msg.cmd) {
        case "ptt_start":
          if (state !== "idle" && state !== "speaking") return;
          setState("listening");
          src = "mic";
          break;
        case "ptt_stop":
          if (state !== "listening") return;
          src = null;
          void turn();
          break;
        case "set_speed":
          speed = Number(msg.speed);
          emit({ event: "speed", speed });
          break;
        case "set_turn_mode":
          emit({ event: "mode", mode: msg.mode as "ptt" });
          setState("idle");
          break;
        case "translate": {
          const id = Number(msg.id);
          const r = turns[id];
          await sleep(300);
          emit({
            event: "translation",
            id,
            ...(r ? r.en : { reponse: "Hello! How are you today?" }),
          });
          break;
        }
        case "vocab": {
          const id = Number(msg.id);
          emit({ event: "vocab", id, items: turns[id]?.vocab ?? [] });
          break;
        }
        case "replay":
          if (state === "idle") await speak(1800);
          break;
        case "stop":
          src = null;
          setState("idle");
          break;
      }
    },
    openPrompts: async () => {},
    openLog: async () => {},
    hide: async () => {},
    onEngine: (cb) => void engineCbs.push(cb),
    onExit: () => {},
    onSetup: () => {},
  };
}
