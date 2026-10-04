<script lang="ts">
  import { memoryEstimate, session } from "../lib/session.svelte";
  import type { Settings } from "../lib/types";

  let { open = $bindable(false) }: { open: boolean } = $props();

  const t = $derived(session.t);
  const s = $derived(t.settings);

  let draft = $state<Settings>($state.snapshot(session.settings!) as Settings);

  // Reset the draft each time the panel opens.
  $effect(() => {
    if (open && session.settings) draft = $state.snapshot(session.settings) as Settings;
  });

  const needsRestart = (a: Settings, b: Settings) =>
    a.stt !== b.stt || a.tts !== b.tts || a.silence !== b.silence || a.prefetch !== b.prefetch;
  const pending = $derived(session.settings ? needsRestart(session.settings, draft) : false);
  const kyutaiStt = $derived(draft.stt === "kyutai" || draft.turn === "semantic");
  const headphonesWarn = $derived(draft.turn !== "ptt" && !session.config?.headphones_likely);

  function change(patch: Partial<Settings>) {
    draft = { ...draft, ...patch };
    if (draft.tts === "kyutai" && (draft.stt === "kyutai" || draft.turn === "semantic")) draft.tts = "kokoro";
    if (!needsRestart(session.settings!, draft)) void session.applySettings(draft);
  }

  function apply() {
    void session.applySettings(draft);
    open = false;
  }
</script>

{#snippet seg<T extends string>(value: T, options: [T, string, boolean?][], onpick: (v: T) => void)}
  <div class="flex rounded-lg bg-white/[0.07] p-0.5">
    {#each options as [v, label, disabled] (v)}
      <button
        class="flex-1 rounded-md px-2 py-1 text-[12px] transition disabled:cursor-not-allowed disabled:opacity-30 {value === v
          ? 'bg-white/90 text-slate-900 shadow'
          : 'text-white/70 hover:text-white'}"
        {disabled}
        onclick={() => onpick(v)}>{label}</button
      >
    {/each}
  </div>
{/snippet}

{#if open}
  <button class="absolute inset-0 z-30 cursor-default bg-black/30" aria-label={s.close} onclick={() => (open = false)}></button>
  <aside class="absolute top-0 right-0 bottom-0 z-40 flex w-[360px] flex-col bg-neutral-900/95 shadow-2xl ring-1 ring-white/10">
    <header data-tauri-drag-region class="flex items-center justify-between px-5 pt-5 pb-3">
      <h2 class="text-sm font-semibold text-white/90">{s.title}</h2>
      <button class="rounded-md px-2 py-0.5 text-white/50 hover:bg-white/10 hover:text-white" onclick={() => (open = false)}>✕</button>
    </header>

    <div class="flex-1 space-y-5 overflow-y-auto px-5 pb-5 text-white/80">
      <section>
        <h3 class="mb-1.5 text-[11px] font-semibold tracking-wider text-white/45 uppercase">{s.turn}</h3>
        {@render seg(
          draft.turn,
          [
            ["ptt", s.turnPtt],
            ["vad", s.turnVad],
            ["semantic", s.turnSemantic, draft.tts === "kyutai"],
          ],
          (v) => change({ turn: v }),
        )}
        <p class="mt-1.5 text-[11px] leading-snug text-white/45">{s.turnHelp[draft.turn]}</p>
        {#if draft.turn === "vad"}
          <label class="mt-2 flex items-center gap-3 text-[11px] text-white/55">
            <span class="flex-1">{s.silence}</span>
            <input
              type="range"
              min="0.6"
              max="3"
              step="0.1"
              value={draft.silence}
              oninput={(e) => change({ silence: Number((e.currentTarget as HTMLInputElement).value) })}
            />
            <span class="w-10 text-right tabular-nums">{draft.silence.toFixed(1)} s</span>
          </label>
        {/if}
        {#if headphonesWarn}
          <p class="mt-2 rounded-md bg-amber-400/10 px-2.5 py-1.5 text-[11px] leading-snug text-amber-200">{s.headphones}</p>
        {/if}
      </section>

      <section>
        <h3 class="mb-1.5 text-[11px] font-semibold tracking-wider text-white/45 uppercase">{s.stt}</h3>
        {@render seg(
          draft.stt,
          [
            ["audio", s.sttAudio],
            ["whisper", s.sttWhisper],
            ["kyutai", s.sttKyutai, draft.tts === "kyutai"],
          ],
          (v) => change({ stt: v }),
        )}
        <p class="mt-1.5 text-[11px] leading-snug text-white/45">{s.sttHelp[draft.stt]}</p>
      </section>

      <section>
        <h3 class="mb-1.5 text-[11px] font-semibold tracking-wider text-white/45 uppercase">{s.tts}</h3>
        {@render seg(
          draft.tts,
          [
            ["kokoro", s.ttsKokoro],
            ["kyutai", s.ttsKyutai, kyutaiStt],
          ],
          (v) => change({ tts: v }),
        )}
        {#if kyutaiStt}
          <p class="mt-1.5 text-[11px] text-white/45">{s.kyutaiBoth}</p>
        {/if}
        {#if kyutaiStt || draft.tts === "kyutai"}
          <p class="mt-1.5 text-[11px] text-white/45">{s.kyutaiNote}</p>
        {/if}
      </section>

      <p class="flex justify-between text-[12px] text-white/60">
        <span>{s.memory}</span>
        <span class="tabular-nums">~{memoryEstimate(draft).toFixed(1)} GB</span>
      </p>

      <section>
        <h3 class="mb-1.5 text-[11px] font-semibold tracking-wider text-white/45 uppercase">{s.lang}</h3>
        {@render seg(
          draft.ui_lang,
          [
            ["fr", "Français"],
            ["en", "English"],
          ],
          (v) => change({ ui_lang: v }),
        )}
      </section>

      <section class="space-y-2 text-[12px]">
        <label class="flex items-start gap-2">
          <input type="checkbox" class="mt-0.5" checked={draft.prefetch} onchange={(e) => change({ prefetch: e.currentTarget.checked })} />
          <span>{s.prefetch}</span>
        </label>
        <label class="flex items-start gap-2">
          <input type="checkbox" class="mt-0.5" checked={draft.greet} onchange={(e) => change({ greet: e.currentTarget.checked })} />
          <span>{s.greet}</span>
        </label>
      </section>

      <section>
        <button class="rounded-lg bg-white/10 px-3 py-1.5 text-[12px] hover:bg-white/20" onclick={() => session.bridge.openPrompts()}>
          {s.prompts}
        </button>
        <p class="mt-1.5 text-[11px] text-white/45">{s.promptsNote}</p>
        <button class="mt-2 rounded-lg bg-white/10 px-3 py-1.5 text-[12px] hover:bg-white/20" onclick={() => session.restart()}>
          {t.restart}
        </button>
      </section>

      {#if session.warnings.length}
        <section>
          <h3 class="mb-1.5 text-[11px] font-semibold tracking-wider text-amber-300/70 uppercase">{s.warnings}</h3>
          {#each session.warnings as w (w)}
            <p class="mb-1 text-[11px] leading-snug text-amber-100/70">{w}</p>
          {/each}
        </section>
      {/if}
    </div>

    {#if pending}
      <footer class="border-t border-white/10 p-4">
        <button class="w-full rounded-lg bg-white py-2 text-sm font-medium text-slate-900 hover:bg-white/90" onclick={apply}>
          {s.restartNeeded}
        </button>
      </footer>
    {/if}
  </aside>
{/if}
