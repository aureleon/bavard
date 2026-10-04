<script lang="ts">
  import { tick } from "svelte";
  import { session } from "../lib/session.svelte";
  import type { Turn, VocabItem } from "../lib/types";

  const t = $derived(session.t);
  let scroller: HTMLDivElement;

  const time = (d: Date) => d.toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit", second: "2-digit" });

  type Segment = { text: string; item?: VocabItem };

  /** Split a reply into plain text and vocabulary highlights. */
  function segments(text: string, items: VocabItem[] | undefined): Segment[] {
    if (!items?.length) return [{ text }];
    const lower = text.toLowerCase();
    const hits: { start: number; end: number; item: VocabItem }[] = [];
    for (const item of items) {
      const i = lower.indexOf(item.fr.toLowerCase());
      if (i < 0) continue;
      const end = i + item.fr.length;
      if (hits.some((h) => i < h.end && end > h.start)) continue;
      hits.push({ start: i, end, item });
    }
    hits.sort((a, b) => a.start - b.start);
    const out: Segment[] = [];
    let at = 0;
    for (const h of hits) {
      if (h.start > at) out.push({ text: text.slice(at, h.start) });
      out.push({ text: text.slice(h.start, h.end), item: h.item });
      at = h.end;
    }
    if (at < text.length) out.push({ text: text.slice(at) });
    return out;
  }

  // Keep the newest line in view.
  $effect(() => {
    void session.turns.length;
    const last = session.turns.at(-1);
    void last?.reponse;
    void last?.correction;
    void session.partial;
    void session.translating;
    tick().then(() => scroller?.scrollTo({ top: scroller.scrollHeight, behavior: "smooth" }));
  });

  function replay(e: MouseEvent, turn: Turn) {
    session.replay(turn.id, e.altKey);
  }
</script>

<div bind:this={scroller} class="flex-1 overflow-y-auto px-6 pt-3 pb-5 [scrollbar-width:thin]">
  <div class="mx-auto flex max-w-xl flex-col gap-5">
    {#each session.turns as turn (turn.id)}
      {#if turn.transcript}
        <section class="group">
          <header class="mb-1 flex items-baseline gap-2 text-[11px] tracking-wide text-white/40">
            <span class="tabular-nums">{time(turn.at)}</span>
            <span class="font-semibold text-white/60 uppercase">{t.you}</span>
          </header>
          {#if session.translating}
            <p class="mb-1 text-[13px] leading-snug text-sky-200/90 italic">
              {turn.translation?.transcript ?? t.translating}
            </p>
          {/if}
          <p class="text-[15px] leading-relaxed text-white/85 select-text">« {turn.transcript} »</p>
          {#if turn.correction}
            <p class="mt-1.5 flex gap-2 text-[13px] leading-snug text-amber-200/90 select-text">
              <span aria-hidden="true" class="text-amber-300/70">✎</span>
              <span>{turn.correction}</span>
            </p>
          {/if}
          {#if turn.pronunciation && !turn.correction?.includes(turn.pronunciation)}
            <p class="mt-1 text-[12px] text-amber-100/60">{t.pronunciation} : {turn.pronunciation}</p>
          {/if}
        </section>
      {/if}

      {#if turn.reponse || turn.streaming}
        <section class="group relative">
          <header class="mb-1 flex items-baseline gap-2 text-[11px] tracking-wide text-white/40">
            <span class="tabular-nums">{time(turn.at)}</span>
            <span class="font-semibold text-sky-300/80 uppercase">{t.tutor}</span>
            {#if !turn.streaming}
              <button
                class="ml-auto rounded-md px-1.5 py-0.5 text-white/40 opacity-0 transition group-hover:opacity-100 hover:bg-white/10 hover:text-white/90"
                class:opacity-100={session.speakingId === turn.id}
                class:text-sky-300={session.speakingId === turn.id}
                title={t.replayHint}
                aria-label={t.replayHint}
                onclick={(e) => replay(e, turn)}
              >
                <svg viewBox="0 0 24 24" class="h-4 w-4" fill="none" stroke="currentColor" stroke-width="1.8">
                  <path d="M4 9v6h4l5 4V5L8 9H4z" stroke-linejoin="round" />
                  <path d="M16 8.5a5 5 0 0 1 0 7M18.5 6a8.5 8.5 0 0 1 0 12" stroke-linecap="round" />
                </svg>
              </button>
            {/if}
          </header>
          {#if session.translating}
            <p class="mb-1 text-[13px] leading-snug text-sky-200/90 italic">
              {turn.translation?.reponse ?? t.translating}
            </p>
          {/if}
          <p class="text-[15px] leading-relaxed text-white select-text">
            {#each segments(turn.reponse, turn.streaming ? undefined : turn.vocab) as seg, i (i)}
              {#if seg.item}
                <span class="vocab" title="{seg.item.lemma} — {seg.item.en}"
                  >{seg.text}<span class="chip">{seg.item.lemma} · {seg.item.en}</span></span
                >
              {:else}{seg.text}{/if}
            {/each}
            {#if turn.streaming}<span class="caret"></span>{/if}
          </p>
        </section>
      {/if}
    {/each}

    {#if session.partial && session.state === "listening"}
      <section>
        <header class="mb-1 flex items-baseline gap-2 text-[11px] tracking-wide text-white/40">
          <span class="font-semibold text-white/60 uppercase">{t.you}</span>
          <span class="text-rose-300/70">● {t.liveHint}</span>
        </header>
        <p class="text-[15px] leading-relaxed text-white/60 italic">{session.partial}</p>
      </section>
    {/if}
  </div>
</div>

<style>
  .vocab {
    position: relative;
    text-decoration: underline;
    text-decoration-color: rgb(110 231 183 / 0.75);
    text-decoration-thickness: 2px;
    text-underline-offset: 4px;
    cursor: help;
  }
  .chip {
    position: absolute;
    left: 0;
    bottom: calc(100% + 6px);
    white-space: nowrap;
    padding: 3px 8px;
    border-radius: 999px;
    font-size: 12px;
    color: rgb(209 250 229);
    background: rgb(6 78 59 / 0.92);
    box-shadow: 0 4px 14px rgb(0 0 0 / 0.3);
    opacity: 0;
    transform: translateY(3px);
    transition: all 120ms ease-out;
    pointer-events: none;
    z-index: 5;
  }
  .vocab:hover .chip {
    opacity: 1;
    transform: none;
  }
  .caret {
    display: inline-block;
    width: 7px;
    height: 1em;
    margin-left: 2px;
    vertical-align: -2px;
    border-radius: 2px;
    background: rgb(125 211 252 / 0.7);
    animation: blink 1s steps(2) infinite;
  }
  @keyframes blink {
    50% {
      opacity: 0;
    }
  }
</style>
