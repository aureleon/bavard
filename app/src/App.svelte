<script lang="ts">
  import { onMount } from "svelte";
  import Controls from "./components/Controls.svelte";
  import Overlay from "./components/Overlay.svelte";
  import SettingsPanel from "./components/SettingsPanel.svelte";
  import Transcript from "./components/Transcript.svelte";
  import { inTauri } from "./lib/bridge";
  import { session } from "./lib/session.svelte";

  const t = $derived(session.t);
  let settingsOpen = $state(false);
  let spaceDown = false;

  onMount(() => {
    void session.init();
  });

  const typing = (e: KeyboardEvent) => {
    const el = e.target as HTMLElement | null;
    return !!el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.isContentEditable);
  };

  function keydown(e: KeyboardEvent) {
    if (e.key === "Escape") {
      if (settingsOpen) settingsOpen = false;
      else void session.bridge?.hide();
      return;
    }
    if (e.metaKey && e.key === ",") {
      e.preventDefault();
      settingsOpen = !settingsOpen;
      return;
    }
    if (settingsOpen || typing(e)) return;
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

<div class="relative flex h-full overflow-hidden bg-neutral-950/55 text-white {inTauri ? '' : 'rounded-[18px] ring-1 ring-white/10'}">
  <!-- Left: the sonic engine -->
  <div class="relative flex w-[40%] min-w-[300px] flex-col border-r border-white/[0.07]">
    <div data-tauri-drag-region class="flex h-11 shrink-0 items-center gap-2 pr-4 {inTauri ? 'pl-[84px]' : 'pl-4'}">
      <span data-tauri-drag-region class="text-[12px] font-semibold tracking-wide text-white/70">Bavard</span>
      {#if session.config}
        <span data-tauri-drag-region class="text-[10px] text-white/30">{session.config.model.split("/").at(-1)}</span>
      {/if}
    </div>
    <div class="min-h-0 flex-1">
      <Controls />
    </div>
  </div>

  <!-- Right: the safety net -->
  <div class="relative flex min-w-0 flex-1 flex-col">
    <div data-tauri-drag-region class="flex h-11 shrink-0 items-center justify-end gap-1 px-3">
      <span data-tauri-drag-region class="mr-auto text-[10px] tracking-wide text-white/35">
        {session.translating ? "EN" : t.shiftHint}
      </span>
      <button
        class="rounded-md p-1.5 text-white/45 hover:bg-white/10 hover:text-white"
        title="{t.settings.title} (⌘,)"
        aria-label={t.settings.title}
        onclick={() => (settingsOpen = true)}
      >
        <svg viewBox="0 0 24 24" class="h-4 w-4" fill="none" stroke="currentColor" stroke-width="1.7">
          <path d="M4 7h10M18 7h2M4 17h4M12 17h8" stroke-linecap="round" />
          <circle cx="16" cy="7" r="2" />
          <circle cx="10" cy="17" r="2" />
        </svg>
      </button>
    </div>

    {#if session.mode !== "ptt" && session.config && !session.config.headphones_likely}
      <p class="mx-6 mb-1 rounded-md bg-amber-400/10 px-3 py-1.5 text-[11px] text-amber-200">{t.settings.headphones}</p>
    {/if}

    <Transcript />

    {#if session.notice}
      <div class="pointer-events-none absolute bottom-5 left-1/2 -translate-x-1/2 rounded-full bg-black/70 px-4 py-1.5 text-[12px] text-white/85 shadow-lg">
        {session.notice.text}
      </div>
    {/if}
  </div>

  <Overlay />
  {#if session.settings}
    <SettingsPanel bind:open={settingsOpen} />
  {/if}
</div>
