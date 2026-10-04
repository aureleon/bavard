<script lang="ts">
  import { onMount } from "svelte";
  import { session } from "../lib/session.svelte";
  import Orb from "./Orb.svelte";

  const t = $derived(session.t);
  let now = $state(performance.now());

  onMount(() => {
    let raf = 0;
    const loop = () => {
      now = performance.now();
      raf = requestAnimationFrame(loop);
    };
    raf = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(raf);
  });

  const elapsed = $derived(session.listeningSince === null ? 0 : (now - session.listeningSince) / 1000);
  const progress = $derived(Math.min(1, elapsed / session.maxAudio));
  const left = $derived(Math.max(0, Math.ceil(session.maxAudio - elapsed)));
  const handsFree = $derived(session.mode !== "ptt");

  const label = $derived.by(() => {
    if (session.phase !== "ready") return t.states.loading;
    if (session.state === "idle" && handsFree) return session.mic ? t.waiting : t.paused;
    return t.states[session.state];
  });

  const fmt = (x: number | null | undefined, unit: string) => (x == null ? "—" : `${x.toFixed(1)} ${unit}`);

  const R = 46;
  const C = 2 * Math.PI * R;

  function down(e: PointerEvent) {
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
    session.pttStart();
  }
</script>

<div class="flex h-full flex-col items-center px-6 pt-4 pb-5">
  <div class="relative min-h-0 w-full flex-1">
    <Orb state={session.phase === "ready" ? session.state : "loading"} mic={session.mic} />
  </div>

  <p class="mb-4 text-[11px] font-semibold tracking-[0.25em] text-white/55">{label}</p>

  {#if handsFree}
    <div class="mb-5 flex h-[104px] flex-col items-center justify-center gap-1 text-center">
      <p class="text-sm font-medium text-white/80">{t.handsFree}</p>
      <p class="text-xs text-white/45">{t.handsFreeHint}</p>
      {#if session.state === "speaking"}
        <button class="mt-2 rounded-full bg-white/10 px-4 py-1 text-xs text-white/80 hover:bg-white/20" onclick={() => session.stopVoice()}>
          {t.stopVoice}
        </button>
      {/if}
    </div>
  {:else}
    <button
      class="relative mb-5 grid h-[104px] w-[104px] place-items-center rounded-full transition active:scale-95 disabled:opacity-40"
      disabled={session.phase !== "ready"}
      onpointerdown={down}
      onpointerup={() => session.pttStop()}
      onpointercancel={() => session.pttStop()}
      aria-label={t.ptt}
    >
      <svg viewBox="0 0 104 104" class="absolute inset-0 -rotate-90">
        <circle cx="52" cy="52" r={R} fill="none" stroke="rgb(255 255 255 / 0.12)" stroke-width="3" />
        {#if session.state === "listening"}
          <circle
            cx="52"
            cy="52"
            r={R}
            fill="none"
            stroke={left <= 5 ? "rgb(251 113 133)" : "rgb(255 160 130)"}
            stroke-width="3"
            stroke-linecap="round"
            stroke-dasharray={C}
            stroke-dashoffset={C * (1 - progress)}
          />
        {/if}
      </svg>
      <span
        class="grid h-[84px] w-[84px] place-items-center rounded-full text-center text-[11px] leading-tight font-semibold tracking-widest transition {session.state ===
        'listening'
          ? 'bg-rose-400 text-white shadow-[0_0_30px_rgb(251_113_133/0.5)]'
          : 'bg-white/90 text-slate-900'}"
      >
        {#if session.state === "listening"}
          {left <= 5 ? t.secondsLeft(left) : t.release}
        {:else}
          {t.ptt}
        {/if}
      </span>
    </button>
    <p class="-mt-3 mb-4 text-[10px] text-white/35">{t.pttHint}</p>
  {/if}

  <label class="mb-3 flex w-full max-w-[260px] items-center gap-3 text-[11px] text-white/55">
    <span class="w-14">{t.speed}</span>
    <input
      type="range"
      min="0.75"
      max="1.25"
      step="0.05"
      value={session.speed}
      oninput={(e) => session.setSpeed(Number((e.currentTarget as HTMLInputElement).value))}
      class="speed flex-1"
    />
    <span class="w-10 text-right tabular-nums text-white/80">{session.speed.toFixed(2)}x</span>
  </label>

  <p class="text-[11px] tabular-nums text-white/40">
    {t.firstAudio} {fmt(session.stats?.first_audio_s, "s")} · {t.ram} {fmt(session.stats?.peak_gb, "GB")} ·
    {session.speed.toFixed(2)}x
  </p>
</div>

<style>
  .speed {
    appearance: none;
    height: 3px;
    border-radius: 3px;
    background: rgb(255 255 255 / 0.18);
    outline: none;
  }
  .speed::-webkit-slider-thumb {
    appearance: none;
    width: 14px;
    height: 14px;
    border-radius: 50%;
    background: white;
    box-shadow: 0 1px 4px rgb(0 0 0 / 0.4);
  }
</style>
