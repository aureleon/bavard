<script lang="ts">
  import { memoryEstimate, session } from "../lib/session.svelte";
  import { modelHearsAudio, type Settings } from "../lib/types";
  import DevOptions from "./DevOptions.svelte";

  const t = $derived(session.t);
  const s = $derived(t.settings);

  let draft = $state<Settings>($state.snapshot(session.settings!) as Settings);


  // Same rule as Settings::needs_restart in settings.rs.
  const needsRestart = (a: Settings, b: Settings) =>
    a.stt !== b.stt ||
    a.tts !== b.tts ||
    a.silence !== b.silence ||
    a.prefetch !== b.prefetch ||
    a.echo !== b.echo ||
    a.barge_in !== b.barge_in ||
    a.dev.model !== b.dev.model ||
    a.dev.whisper_model !== b.dev.whisper_model ||
    a.dev.kyutai_bits !== b.dev.kyutai_bits ||
    a.dev.kyutai_stt_bits !== b.dev.kyutai_stt_bits ||
    a.dev.verbose !== b.dev.verbose;
  const pending = $derived(session.settings ? needsRestart(session.settings, draft) : false);
  const kyutaiStt = $derived(draft.stt === "kyutai" || draft.turn === "semantic");
  // An option is disabled when picking it would go past the GPU memory that
  // macOS recommends on this Mac (Metal recommendedMaxWorkingSetSize).
  const over = (patch: Partial<Settings>) => !session.fits({ ...draft, ...patch });
  const budget = $derived(session.budgetGb);
  // Speakers and no echo cancellation: the tutor's voice can start a turn.
  const echoOn = $derived(
    draft.echo === "on" ||
      (draft.echo === "auto" && !session.config?.headphones_likely) ||
      false,
  );
  const echoMissing = $derived(
    echoOn && draft.echo === session.settings?.echo && session.config !== null && !session.config.echo_cancel,
  );
  const headphonesWarn = $derived(
    draft.turn !== "ptt" && !session.config?.headphones_likely && (!echoOn || echoMissing),
  );
  const echoStatus = $derived(
    echoMissing ? s.echoMissing : echoOn ? s.echoActive : session.config?.headphones_likely ? s.echoHeadphones : s.echoOff,
  );

  function change(patch: Partial<Settings>) {
    draft = { ...draft, ...patch };
    if (!session.fits(draft)) draft.tts = "kokoro";
    if (!needsRestart(session.settings!, draft)) void session.applySettings(draft);
  }

  function apply() {
    void session.applySettings(draft);
    session.view = "chat";
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

<div class="flex min-h-0 flex-1 flex-col">
    <div class="mx-auto w-full max-w-md flex-1 space-y-5 overflow-y-auto px-6 pt-2 pb-5 text-white/80">
      <section>
        <h3 class="mb-1.5 text-[11px] font-semibold tracking-wider text-white/45 uppercase">{s.turn}</h3>
        {@render seg(
          draft.turn,
          [
            ["ptt", s.turnPtt],
            ["vad", s.turnVad, over({ turn: "vad" })],
            ["semantic", s.turnSemantic, over({ turn: "semantic" })],
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
        <h3 class="mb-1.5 text-[11px] font-semibold tracking-wider text-white/45 uppercase">{s.echo}</h3>
        {@render seg(
          draft.echo,
          [
            ["auto", s.echoAuto],
            ["on", s.echoOnLabel],
            ["off", s.echoOffLabel],
          ],
          (v) => change({ echo: v as Settings["echo"] }),
        )}
        <p class="mt-1.5 text-[11px] leading-snug text-white/45">{echoStatus}</p>
        {#if draft.turn !== "ptt"}
          <label class="mt-2 flex items-start gap-2 text-[12px] text-white/75">
            <input
              type="checkbox"
              class="mt-0.5"
              checked={draft.barge_in}
              onchange={(e) => change({ barge_in: e.currentTarget.checked })}
            />
            <span>
              {s.bargeIn}
              <span class="block text-[11px] text-white/45">{echoOn || session.config?.headphones_likely ? s.bargeInHelp : s.bargeInNeeds}</span>
            </span>
          </label>
        {/if}
      </section>

      <section>
        <h3 class="mb-1.5 text-[11px] font-semibold tracking-wider text-white/45 uppercase">{s.stt}</h3>
        {@render seg(
          draft.stt,
          [
            ["audio", s.sttAudio, !modelHearsAudio(draft.dev.model)],
            ["whisper", s.sttWhisper, over({ stt: "whisper" })],
            ["kyutai", s.sttKyutai, over({ stt: "kyutai" })],
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
            ["kyutai", s.ttsKyutai, over({ tts: "kyutai" })],
          ],
          (v) => change({ tts: v }),
        )}
        {#if over({ tts: "kyutai" }) && budget}
          <p class="mt-1.5 text-[11px] text-white/45">
            {s.overBudget(memoryEstimate({ ...draft, tts: "kyutai" }), budget)}
          </p>
        {/if}
        {#if kyutaiStt || draft.tts === "kyutai"}
          <p class="mt-1.5 text-[11px] text-white/45">{s.kyutaiNote}</p>
        {/if}
      </section>

      <div class="text-[12px] text-white/60">
        <p class="flex justify-between">
          <span>{s.memory}</span>
          <span class="tabular-nums">~{memoryEstimate(draft).toFixed(1)} GB</span>
        </p>
        {#if budget}
          <div class="mt-1.5 h-1 overflow-hidden rounded-full bg-white/10">
            <div class="h-full rounded-full bg-sky-300/70" style:width="{Math.min(100, (100 * memoryEstimate(draft)) / budget)}%"></div>
          </div>
          <p class="mt-1 text-right text-[10px] text-white/40">{s.memoryOf(budget)}</p>
        {/if}
      </div>

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
        <div class="flex gap-2">
          <button class="rounded-lg bg-white/10 px-3 py-1.5 text-[12px] hover:bg-white/20" onclick={() => (session.view = "prompts")}>
            {s.prompts}
          </button>
          <button class="rounded-lg bg-white/10 px-3 py-1.5 text-[12px] hover:bg-white/20" onclick={() => session.restart()}>
            {t.restart}
          </button>
        </div>
        <p class="mt-1.5 text-[11px] text-white/45">{s.promptsNote}</p>
      </section>

      <DevOptions {draft} {change} />

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
      <footer class="mx-auto w-full max-w-md border-t border-white/10 px-6 py-4">
        <button class="w-full rounded-lg bg-white py-2 text-sm font-medium text-slate-900 hover:bg-white/90" onclick={apply}>
          {s.restartNeeded}
        </button>
      </footer>
    {/if}
</div>
