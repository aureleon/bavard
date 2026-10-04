<script lang="ts">
  import { onMount } from "svelte";
  import Controls from "./components/Controls.svelte";
  import Overlay from "./components/Overlay.svelte";
  import PromptsView from "./components/PromptsView.svelte";
  import SettingsView from "./components/SettingsView.svelte";
  import Transcript from "./components/Transcript.svelte";
  import { inTauri } from "./lib/bridge";
  import { session, type View } from "./lib/session.svelte";

  const t = $derived(session.t);
  let spaceDown = false;

  onMount(() => {
    void session.init();
  });

  const typing = (e: KeyboardEvent) => {
    const el = e.target as HTMLElement | null;
    return !!el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.isContentEditable);
  };

  const go = (v: View) => (session.view = v);

  function keydown(e: KeyboardEvent) {
    if (e.key === "Escape") {
      if (session.view !== "chat") go("chat");
      else void session.bridge?.hide();
      return;
    }
    if (e.metaKey && e.key === ",") {
      e.preventDefault();
      go(session.view === "settings" ? "chat" : "settings");
      return;
    }
    if (e.metaKey && (e.key === "1" || e.key === "2" || e.key === "3")) {
      e.preventDefault();
      go((["chat", "prompts", "settings"] as const)[Number(e.key) - 1]);
      return;
    }
    if (typing(e)) return;
    if (e.code === "Space") {
      e.preventDefault();
      if (!e.repeat && !spaceDown) {
        spaceDown = true;
        session.pttStart();
      }
    } else if (e.key === "Shift" && !e.repeat) {
      session.setTranslating(true);
    } else if (e.key === "+" || e.key === "=") {
      session.setSpeed(Math.min(1.25, Math.round((session.speed + 0.05) * 100) / 100));
    } else if (e.key === "-") {
      session.setSpeed(Math.max(0.75, Math.round((session.speed - 0.05) * 100) / 100));
    } else if (e.key === "r" && !e.metaKey) {
      const last = session.turns.findLast((x) => x.reponse && !x.streaming);
      if (last) session.replay(last.id, e.altKey);
    }
  }

  function keyup(e: KeyboardEvent) {
    if (e.code === "Space" && spaceDown) {
      spaceDown = false;
      session.pttStop();
    } else if (e.key === "Shift") {
      session.setTranslating(false);
    }
  }

  // Releasing keys while the window is in the background never sends keyup.
  function blur() {
    if (spaceDown) {
      spaceDown = false;
      session.pttStop();
    }
    session.setTranslating(false);
  }
</script>

<svelte:window onkeydown={keydown} onkeyup={keyup} onblur={blur} />

<div
  class="relative flex h-full flex-col overflow-hidden bg-neutral-950/55 text-white {inTauri
    ? ''
    : 'rounded-[12px] ring-1 ring-white/10'}"
>
  <!-- Unified title bar: traffic lights, title, navigation, settings -->
  <header
    data-tauri-drag-region
    class="relative grid h-12 shrink-0 grid-cols-[1fr_auto_1fr] items-center border-b border-white/[0.07] bg-white/[0.03] pr-3 {inTauri
      ? 'pl-[92px]'
      : 'pl-4'}"
  >
    {#if !inTauri}
      <!-- Browser preview only: stand-ins for the native traffic lights -->
      <div class="absolute top-1/2 left-4 flex -translate-y-1/2 gap-2" aria-hidden="true">
        <span class="h-3 w-3 rounded-full bg-[#ff5f57]"></span>
        <span class="h-3 w-3 rounded-full bg-[#febc2e]"></span>
        <span class="h-3 w-3 rounded-full bg-[#28c840]"></span>
      </div>
    {/if}
    <div data-tauri-drag-region class="flex min-w-0 items-baseline gap-2 {inTauri ? '' : 'pl-[64px]'}">
      <span data-tauri-drag-region class="text-[13px] font-semibold text-white/85">Bavard</span>
      {#if session.config}
        <span data-tauri-drag-region class="truncate text-[11px] text-white/35">{session.config.model.split("/").at(-1)}</span>
      {/if}
    </div>

    <nav class="flex rounded-full bg-black/25 p-[3px] ring-1 ring-white/[0.06]" aria-label="Navigation">
      {#each [["chat", t.nav.chat, "⌘1"], ["prompts", t.nav.prompts, "⌘2"]] as const as [v, label, key] (v)}
        <button
          class="rounded-full px-3.5 py-[3px] text-[12px] font-medium transition {session.view === v
            ? 'bg-white/[0.16] text-white shadow-sm'
            : 'text-white/55 hover:text-white/85'}"
          title={key}
          aria-current={session.view === v ? "page" : undefined}
          onclick={() => go(v)}>{label}</button
        >
      {/each}
    </nav>

    <div data-tauri-drag-region class="flex items-center justify-end gap-2">
      <button
        class="rounded-md p-1.5 transition {session.view === 'settings'
          ? 'bg-white/[0.16] text-white'
          : 'text-white/50 hover:bg-white/10 hover:text-white'}"
        title="{t.settings.title} (⌘,)"
        aria-label={t.settings.title}
        aria-pressed={session.view === "settings"}
        onclick={() => go(session.view === "settings" ? "chat" : "settings")}
      >
        <svg viewBox="0 0 24 24" class="h-[17px] w-[17px]" fill="none" stroke="currentColor" stroke-width="1.6">
          <path
            d="M10.3 3.6a1.7 1.7 0 0 1 3.4 0l.2.9a1.7 1.7 0 0 0 2.5 1l.8-.4a1.7 1.7 0 0 1 2.4 2.4l-.4.8a1.7 1.7 0 0 0 1 2.5l.9.2a1.7 1.7 0 0 1 0 3.4l-.9.2a1.7 1.7 0 0 0-1 2.5l.4.8a1.7 1.7 0 0 1-2.4 2.4l-.8-.4a1.7 1.7 0 0 0-2.5 1l-.2.9a1.7 1.7 0 0 1-3.4 0l-.2-.9a1.7 1.7 0 0 0-2.5-1l-.8.4a1.7 1.7 0 0 1-2.4-2.4l.4-.8a1.7 1.7 0 0 0-1-2.5l-.9-.2a1.7 1.7 0 0 1 0-3.4l.9-.2a1.7 1.7 0 0 0 1-2.5l-.4-.8a1.7 1.7 0 0 1 2.4-2.4l.8.4a1.7 1.7 0 0 0 2.5-1z"
            stroke-linejoin="round"
          />
          <circle cx="12" cy="12" r="3" />
        </svg>
      </button>
    </div>
  </header>

  <div class="flex min-h-0 flex-1">
    <!-- Left: the sonic engine -->
    <div class="relative flex w-[40%] min-w-[300px] flex-col border-r border-white/[0.07]">
      <Controls />
    </div>

    <!-- Right: the safety net, prompts or settings -->
    <div class="relative flex min-w-0 flex-1 flex-col">
      {#if session.view === "chat"}
        {#if session.mode !== "ptt" && session.config && !session.config.headphones_likely}
          <p class="mx-6 mt-3 rounded-md bg-amber-400/10 px-3 py-1.5 text-[11px] text-amber-200">{t.settings.headphones}</p>
        {/if}
        <Transcript />
        <span
          class="pointer-events-none absolute top-3 right-4 rounded-full px-2.5 py-0.5 text-[11px] transition {session.translating
            ? 'bg-sky-300/15 text-sky-200'
            : 'bg-white/[0.06] text-white/40'}"
        >
          {session.translating ? "EN" : t.shiftHint}
        </span>
      {:else if session.view === "prompts"}
        <PromptsView />
      {:else if session.settings}
        <SettingsView />
      {/if}

      {#if session.notice}
        <div class="pointer-events-none absolute bottom-5 left-1/2 -translate-x-1/2 rounded-full bg-black/70 px-4 py-1.5 text-[12px] whitespace-nowrap text-white/85 shadow-lg">
          {session.notice.text}
        </div>
      {/if}
    </div>
  </div>

  <Overlay />
</div>
