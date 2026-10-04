// Thin layer over Tauri IPC. Outside Tauri (plain `pnpm dev` in a browser)
// it falls back to a scripted mock engine, so the UI can be developed and
// screenshotted without the models.

import type { EngineEvent, EngineExit, Settings, SetupEvent } from "./types";

export interface Bridge {
  getSettings(): Promise<Settings>;
  saveSettings(settings: Settings): Promise<boolean>;
  start(): Promise<void>;
  restart(): Promise<void>;
  send(msg: Record<string, unknown>): Promise<void>;
  openPrompts(): Promise<void>;
  openLog(): Promise<void>;
  hide(): Promise<void>;
  onEngine(cb: (e: EngineEvent) => void): void;
  onExit(cb: (e: EngineExit) => void): void;
  onSetup(cb: (e: SetupEvent) => void): void;
}

export const inTauri = typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;

async function tauriBridge(): Promise<Bridge> {
  const { invoke } = await import("@tauri-apps/api/core");
  const { listen } = await import("@tauri-apps/api/event");
  return {
    getSettings: () => invoke<Settings>("get_settings"),
    saveSettings: (settings) => invoke<boolean>("save_settings", { settings }),
    start: () => invoke("engine_start"),
    restart: () => invoke("engine_restart"),
    send: (msg) => invoke("engine_send", { msg }),
    openPrompts: () => invoke("open_prompts"),
    openLog: () => invoke("open_log"),
    hide: () => invoke("hide_window"),
    onEngine: (cb) => void listen<EngineEvent>("engine", (e) => cb(e.payload)),
    onExit: (cb) => void listen<EngineExit>("engine-exit", (e) => cb(e.payload)),
    onSetup: (cb) => void listen<SetupEvent>("setup", (e) => cb(e.payload)),
  };
}

export async function getBridge(): Promise<Bridge> {
  if (inTauri) return tauriBridge();
  const { mockBridge } = await import("./mock");
  return mockBridge();
}
