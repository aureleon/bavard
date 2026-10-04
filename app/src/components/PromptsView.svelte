<script lang="ts">
  import { onMount } from "svelte";
  import { session } from "../lib/session.svelte";
  import { PROMPT_MARKERS, type PromptName, type Prompts } from "../lib/types";

  const t = $derived(session.t);
  const p = $derived(t.prompts);

  let files = $state<Prompts | null>(null);
  let drafts = $state<Record<PromptName, string>>({ tutor: "", hear: "" });
  let tab = $state<PromptName>("tutor");
  let saving = $state(false);
  let error = $state<string | null>(null);
  let lang = $state<"fr" | "en">("fr");

  // English drafts are the learner's own source text: keep them across launches.
  const EN_KEY = (n: PromptName) => `bavard.prompt.en.${n}`;
  let english = $state<Record<PromptName, string>>({
    tutor: localStorage.getItem(EN_KEY("tutor")) ?? "",
    hear: localStorage.getItem(EN_KEY("hear")) ?? "",
  });
  $effect(() => {
    for (const n of ["tutor", "hear"] as const) localStorage.setItem(EN_KEY(n), english[n]);
  });

  const tr = $derived(session.promptTranslation);
  const translatingHere = $derived(!!tr && !tr.done && tr.name === tab);
  let appliedReq = 0;
  // A finished translation becomes the French draft, for review before saving.
  $effect(() => {
    if (tr?.done && tr.req !== appliedReq) {
      appliedReq = tr.req;
      if (tr.text) drafts[tr.name] = tr.text;
    }
  });

  function translate() {
    if (!english[tab].trim()) return;
    session.translatePrompt(tab, english[tab]);
    lang = "fr";
  }

  async function load() {
    try {
      files = await session.bridge.readPrompts();
      drafts = { tutor: files.tutor.text, hear: files.hear.text };
      error = null;
    } catch (e) {
      error = String(e);
    }
  }
  onMount(load);

  const text = $derived(translatingHere ? (tr?.text ?? "") : drafts[tab]);
  const dirty = $derived(!!files && drafts[tab] !== files[tab].text);
  const isDefault = $derived(!!files && drafts[tab].trim() === files[tab].default.trim());
  const markers = $derived(
    PROMPT_MARKERS[tab].map((m) => ({ m, ok: text.toLowerCase().includes(m.toLowerCase()) })),
  );
  const missing = $derived(markers.some((x) => !x.ok));
  const unused = $derived(tab === "hear" && session.config && session.config.stt !== "audio");

  async function save() {
    if (!files || saving) return;
    saving = true;
    try {
      await session.bridge.writePrompt(tab, drafts[tab]);
      files[tab].text = drafts[tab];
      error = null;
    } catch (e) {
      error = String(e);
    } finally {
      saving = false;
    }
  }

  function keydown(e: KeyboardEvent) {
    if (e.metaKey && e.key === "s") {
      e.preventDefault();
      void save();
    }
  }
</script>

<div class="flex min-h-0 flex-1 flex-col px-6 pt-2 pb-5">
  <div class="mb-3 flex items-center gap-3">
    <div class="flex rounded-full bg-white/[0.07] p-0.5">
      {#each ["tutor", "hear"] as const as name (name)}
        <button
          class="rounded-full px-3 py-1 text-[12px] transition {tab === name
            ? 'bg-white/90 text-slate-900 shadow'
            : 'text-white/65 hover:text-white'}"
          onclick={() => (tab = name)}
        >
          {p[name]}
          {#if files && drafts[name] !== files[name].text}<span class="ml-1 text-amber-500">●</span>{/if}
        </button>
      {/each}
    </div>
    <p class="min-w-0 flex-1 truncate text-[11px] text-white/40" title={files?.[tab].path}>{files?.[tab].path ?? ""}</p>
    <div class="flex shrink-0 rounded-full bg-white/[0.07] p-0.5">
      {#each [["fr", p.langFr], ["en", p.langEn]] as const as [v, label] (v)}
        <button
          class="rounded-full px-2.5 py-0.5 text-[11px] transition {lang === v
            ? 'bg-white/90 text-slate-900 shadow'
            : 'text-white/60 hover:text-white'}"
          onclick={() => (lang = v)}>{label}</button
        >
      {/each}
    </div>
  </div>

  <p class="mb-2 text-[11px] leading-snug text-white/50">
    {#if lang === "en"}
      {p.enHelp}
    {:else}
      {tab === "tutor" ? p.tutorHelp : p.hearHelp}
      {#if unused}<span class="text-amber-200/80"> {p.hearUnused}</span>{/if}
    {/if}
  </p>

  {#if lang === "en"}
    <textarea
      class="min-h-0 flex-1 resize-none rounded-xl bg-black/35 p-4 font-mono text-[12px] leading-relaxed text-white/85 ring-1 ring-sky-300/20 outline-none select-text placeholder:text-white/25 focus:ring-sky-300/40"
      spellcheck="true"
      lang="en"
      placeholder={p.enPlaceholder}
      bind:value={english[tab]}
    ></textarea>
    <div class="mt-3 flex items-center gap-3">
      <button
        class="rounded-lg bg-sky-300 px-3 py-1.5 text-[12px] font-medium text-slate-900 hover:bg-sky-200 disabled:opacity-40"
        disabled={!english[tab].trim() || (!!tr && !tr.done) || session.phase !== "ready"}
        onclick={translate}>{p.translate}</button
      >
      <p class="text-[10px] text-white/40">{p.translateBusy}</p>
    </div>
  {:else}
    <textarea
      class="min-h-0 flex-1 resize-none rounded-xl bg-black/35 p-4 font-mono text-[12px] leading-relaxed text-white/85 ring-1 outline-none select-text focus:ring-sky-300/40 {translatingHere
        ? 'ring-sky-300/40'
        : 'ring-white/10'}"
      spellcheck="false"
      lang="fr"
      value={translatingHere ? tr!.text : drafts[tab]}
      oninput={(e) => (drafts[tab] = e.currentTarget.value)}
      onkeydown={keydown}
      readonly={translatingHere}
      disabled={!files}
    ></textarea>
    {#if translatingHere}
      <p class="mt-2 flex items-center gap-2 text-[11px] text-sky-200/80"><span class="dot"></span>{p.translating}</p>
    {:else if tr?.done && tr.name === tab && drafts[tab] === tr.text}
      <p class="mt-2 text-[11px] {tr.missing?.length ? 'text-amber-200/80' : 'text-sky-200/80'}">
        {tr.missing?.length ? p.translateMissing(tr.missing.join(", ")) : p.translated}
      </p>
    {/if}

  <div class="mt-3 flex flex-wrap items-center gap-2 text-[11px]">
    <span class="text-white/45">{p.markers}</span>
    {#each markers as { m, ok } (m)}
      <span
        class="rounded-full px-2 py-0.5 font-mono {ok ? 'bg-emerald-400/15 text-emerald-200' : 'bg-amber-400/15 text-amber-200'}"
        >{ok ? "✓" : "!"} {m}</span
      >
    {/each}
  </div>
  {#if missing}
    <p class="mt-1.5 text-[11px] text-amber-200/80">{p.missing}</p>
  {/if}
  {#if error}
    <p class="mt-1.5 text-[11px] text-rose-300 select-text">{error}</p>
  {/if}

  <div class="mt-3 flex items-center gap-2">
    <button
      class="rounded-lg bg-white px-3 py-1.5 text-[12px] font-medium text-slate-900 hover:bg-white/90 disabled:opacity-40"
      disabled={!dirty || saving}
      title="⌘S"
      onclick={save}>{p.save}</button
    >
    <button
      class="rounded-lg bg-white/10 px-3 py-1.5 text-[12px] hover:bg-white/20 disabled:opacity-40"
      disabled={!dirty}
      onclick={() => files && (drafts[tab] = files[tab].text)}>{p.revert}</button
    >
    <button
      class="rounded-lg bg-white/10 px-3 py-1.5 text-[12px] hover:bg-white/20 disabled:opacity-40"
      disabled={isDefault}
      onclick={() => files && (drafts[tab] = files[tab].default)}>{p.restore}</button
    >
    <button class="ml-auto rounded-lg px-2 py-1.5 text-[12px] text-white/50 hover:bg-white/10 hover:text-white" onclick={() => session.bridge.openPrompts()}>
      {p.openFolder}
    </button>
  </div>
  <p class="mt-2 text-[10px] text-white/35">{p.applyNote}</p>
  {/if}
</div>

<style>
  .dot {
    width: 6px;
    height: 6px;
    border-radius: 50%;
    background: rgb(125 211 252);
    animation: pulse 0.9s ease-in-out infinite alternate;
  }
  @keyframes pulse {
    to {
      opacity: 0.25;
    }
  }
</style>
