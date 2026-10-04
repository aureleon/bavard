<script lang="ts">
  import { onMount } from "svelte";
  import { session } from "../lib/session.svelte";
  import { MODELS, modelHearsAudio, type DevSettings, type Settings } from "../lib/types";

  // Developer options: model choice and tuning. Sampling, reply length,
  // context and the end-of-turn threshold are live; the rest restarts the
  // engine (through the parent's Apply button).

  let { draft, change }: { draft: Settings; change: (patch: Partial<Settings>) => void } = $props();

  const t = $derived(session.t);
  const d = $derived(t.dev);
  let open = $state(false);

  const DEFAULTS: DevSettings = {
    model: "gemma-e4b",
    max_context: 16384,
    temperature: null,
    top_p: null,
    top_k: null,
    max_tokens: 220,
    eot_threshold: 0.5,
    whisper_model: "mlx-community/whisper-base-mlx",
    kyutai_bits: 8,
    kyutai_stt_bits: 8,
    verbose: false,
  };
  const CONTEXTS = [4096, 8192, 16384, 32768];
  const WHISPERS = [
    ["mlx-community/whisper-base-mlx", "base (74M)"],
    ["mlx-community/whisper-small-mlx", "small (244M)"],
    ["mlx-community/whisper-large-v3-turbo", "large-v3-turbo"],
  ] as const;
  const BITS = [
    [8, "q8"],
    [4, "q4"],
    [0, "bf16"],
  ] as const;

  const custom = $derived(!MODELS.some((m) => m.id === draft.dev.model));
  let customRepo = $state("");
  const effective = $derived(session.config?.tuning);

  function dev(patch: Partial<DevSettings>) {
    const next = { ...draft.dev, ...patch };
    const extra: Partial<Settings> = {};
    // A text-only model cannot do the Gemma audio pass.
    if (patch.model !== undefined && draft.stt === "audio" && !modelHearsAudio(next.model)) extra.stt = "whisper";
    change({ dev: next, ...extra });
  }

  // Context use, for the context row.
  onMount(() => {
    if (session.phase === "ready") session.send({ cmd: "stats" });
  });

  const num = (e: Event) => Number((e.currentTarget as HTMLInputElement).value);
</script>

{#snippet row(label: string, live: boolean)}
  <span class="flex items-center gap-1.5">
    {label}
    <span
      class="h-1.5 w-1.5 rounded-full {live ? 'bg-emerald-300/70' : 'bg-amber-300/70'}"
      title={live ? d.live : d.restart}
    ></span>
  </span>
{/snippet}

<section class="rounded-xl bg-white/[0.04] ring-1 ring-white/[0.06]">
  <button class="flex w-full items-center justify-between px-3 py-2 text-left" onclick={() => (open = !open)}>
    <span class="text-[11px] font-semibold tracking-wider text-white/55 uppercase">{d.title}</span>
    <span class="text-white/40 transition {open ? 'rotate-90' : ''}">›</span>
  </button>

  {#if open}
    <div class="space-y-3.5 px-3 pb-3 text-[12px] text-white/75">
      <p class="text-[11px] leading-snug text-white/45">{d.note}</p>
      <p class="flex gap-3 text-[10px] text-white/45">
        <span class="flex items-center gap-1"><span class="h-1.5 w-1.5 rounded-full bg-emerald-300/70"></span>{d.live}</span>
        <span class="flex items-center gap-1"><span class="h-1.5 w-1.5 rounded-full bg-amber-300/70"></span>{d.restart}</span>
      </p>

      <!-- Model -->
      <label class="block">
        <span class="mb-1 block text-white/55">{@render row(d.model, false)}</span>
        <select
          class="w-full rounded-md bg-black/30 px-2 py-1 ring-1 ring-white/10"
          value={custom ? "__custom" : draft.dev.model}
          onchange={(e) => {
            const v = e.currentTarget.value;
            if (v === "__custom") {
              customRepo = custom ? draft.dev.model : "";
              if (customRepo) dev({ model: customRepo });
            } else dev({ model: v });
          }}
        >
          {#each MODELS as m (m.id)}
            <option value={m.id}>{m.label}{m.id === DEFAULTS.model ? ` (${d.auto})` : ""}{m.audio ? "" : ` — ${d.textTag}`}</option>
          {/each}
          <option value="__custom">{d.custom}</option>
        </select>
        {#if custom || customRepo !== ""}
          <input
            class="mt-1.5 w-full rounded-md bg-black/30 px-2 py-1 font-mono text-[11px] ring-1 ring-white/10 select-text"
            placeholder={d.customPlaceholder}
            value={custom ? draft.dev.model : customRepo}
            onchange={(e) => e.currentTarget.value.trim() && dev({ model: e.currentTarget.value.trim() })}
          />
        {/if}
        {#if !modelHearsAudio(draft.dev.model)}
          <p class="mt-1 text-[11px] text-amber-200/80">{d.textOnly}</p>
        {/if}
        {#if draft.dev.model !== DEFAULTS.model}
          <p class="mt-1 text-[11px] text-white/40">{d.memoryNote}</p>
        {/if}
      </label>

      <!-- Context -->
      <label class="block">
        <span class="mb-1 flex justify-between text-white/55">
          {@render row(d.context, true)}
          {#if session.contextTokens !== null}
            <span class="text-[10px] text-white/40 tabular-nums">{d.contextUsed(session.contextTokens, draft.dev.max_context)}</span>
          {/if}
        </span>
        <div class="flex rounded-lg bg-white/[0.07] p-0.5">
          {#each CONTEXTS as c (c)}
            <button
              class="flex-1 rounded-md py-0.5 text-[11px] tabular-nums transition {draft.dev.max_context === c
                ? 'bg-white/90 text-slate-900'
                : 'text-white/65 hover:text-white'}"
              onclick={() => dev({ max_context: c })}>{c / 1024}K</button
            >
          {/each}
        </div>
      </label>

      <!-- Sampling -->
      {#each [
        { key: "temperature", label: d.temperature, min: 0, max: 1.5, step: 0.05, fmt: (v: number) => v.toFixed(2) },
        { key: "top_p", label: d.topP, min: 0.5, max: 1, step: 0.01, fmt: (v: number) => v.toFixed(2) },
        { key: "top_k", label: d.topK, min: 1, max: 200, step: 1, fmt: (v: number) => String(v) },
      ] as const as s (s.key)}
        {@const value = draft.dev[s.key]}
        {@const shown = value ?? effective?.[s.key] ?? null}
        <div>
          <div class="mb-1 flex items-center justify-between text-white/55">
            {@render row(s.label, true)}
            <label class="flex items-center gap-1 text-[10px] text-white/45">
              <input type="checkbox" checked={value === null} onchange={(e) => dev({ [s.key]: e.currentTarget.checked ? null : (shown ?? s.max) })} />
              {d.auto}
            </label>
          </div>
          <div class="flex items-center gap-3">
            <input
              type="range"
              class="flex-1"
              min={s.min}
              max={s.max}
              step={s.step}
              disabled={value === null}
              value={shown ?? s.min}
              onchange={(e) => dev({ [s.key]: num(e) })}
            />
            <span class="w-10 text-right text-[11px] tabular-nums {value === null ? 'text-white/35' : ''}">
              {shown === null ? "off" : s.fmt(shown)}
            </span>
          </div>
        </div>
      {/each}

      <!-- Reply length -->
      <div>
        <div class="mb-1 text-white/55">{@render row(d.maxTokens, true)}</div>
        <div class="flex items-center gap-3">
          <input type="range" class="flex-1" min="80" max="600" step="20" value={draft.dev.max_tokens} onchange={(e) => dev({ max_tokens: num(e) })} />
          <span class="w-10 text-right text-[11px] tabular-nums">{draft.dev.max_tokens}</span>
        </div>
      </div>

      <!-- Semantic end of turn -->
      <div>
        <div class="mb-1 text-white/55">{@render row(d.eot, true)}</div>
        <div class="flex items-center gap-3">
          <input
            type="range"
            class="flex-1"
            min="0.2"
            max="0.95"
            step="0.05"
            value={draft.dev.eot_threshold}
            onchange={(e) => dev({ eot_threshold: num(e) })}
          />
          <span class="w-10 text-right text-[11px] tabular-nums">{draft.dev.eot_threshold.toFixed(2)}</span>
        </div>
        <p class="mt-0.5 text-[10px] text-white/40">{d.eotHelp}</p>
      </div>

      <!-- Whisper / Kyutai -->
      <label class="block">
        <span class="mb-1 block text-white/55">{@render row(d.whisper, false)}</span>
        <select
          class="w-full rounded-md bg-black/30 px-2 py-1 ring-1 ring-white/10"
          value={draft.dev.whisper_model}
          onchange={(e) => dev({ whisper_model: e.currentTarget.value })}
        >
          {#each WHISPERS as [id, label] (id)}
            <option value={id}>{label}</option>
          {/each}
        </select>
      </label>
      {#each [["kyutai_bits", d.kyutaiBits], ["kyutai_stt_bits", d.kyutaiSttBits]] as const as [key, label] (key)}
        <div>
          <div class="mb-1 text-white/55">{@render row(label, false)}</div>
          <div class="flex rounded-lg bg-white/[0.07] p-0.5">
            {#each BITS as [b, bl] (b)}
              <button
                class="flex-1 rounded-md py-0.5 text-[11px] transition {draft.dev[key] === b
                  ? 'bg-white/90 text-slate-900'
                  : 'text-white/65 hover:text-white'}"
                onclick={() => dev({ [key]: b })}>{bl}</button
              >
            {/each}
          </div>
        </div>
      {/each}

      <label class="flex items-start gap-2">
        <input type="checkbox" class="mt-0.5" checked={draft.dev.verbose} onchange={(e) => dev({ verbose: e.currentTarget.checked })} />
        <span>{@render row(d.verbose, false)}</span>
      </label>

      <button class="rounded-lg bg-white/10 px-3 py-1.5 text-[12px] hover:bg-white/20" onclick={() => dev({ ...DEFAULTS })}>
        {d.reset}
      </button>
    </div>
  {/if}
</section>
