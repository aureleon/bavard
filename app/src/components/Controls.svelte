<script lang="ts">
  import { onMount } from "svelte";
  import { session } from "../lib/session.svelte";
  import SphereWave from "./SphereWave.svelte";

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
    if (session.voicePaused) return t.voicePaused;
    if (session.state === "idle" && handsFree) return session.listeningOff ? t.micOff : t.waiting;
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
    <SphereWave state={session.phase === "ready" ? session.state : "loading"} mic={session.mic} />
  </div>

  <p class="mb-1 text-[11px] font-semibold tracking-[0.25em] text-white/55">{label}</p>
  <p class="mb-3 h-3 text-[10px] text-white/35">
    {#if session.voicePaused}{t.escResume}{:else if session.state === "speaking"}{t.escPause}{/if}
  </p>

  {#if handsFree}
    <!-- Hands-free: the round button switches listening on and off -->
    <button
      class="relative mb-5 grid h-[104px] w-[104px] place-items-center rounded-full transition active:scale-95 disabled:opacity-40"
      disabled={session.phase !== "ready"}
      onclick={() => session.toggleListening()}
      aria-pressed={!session.listeningOff}
      aria-label={session.listeningOff ? t.micOn : t.micOffAction}
    >
      <svg viewBox="0 0 104 104" class="absolute inset-0">
        <circle cx="52" cy="52" r={R} fill="none" stroke="rgb(255 255 255 / 0.12)" stroke-width="3" />
        {#if !session.listeningOff}
          <circle cx="52" cy="52" r={R} fill="none" stroke="rgb(110 231 183 / 0.6)" stroke-width="3" />
        {/if}
      </svg>
      <span
        class="grid h-[84px] w-[84px] place-items-center rounded-full text-center text-[10px] leading-tight font-semibold tracking-wider transition {session.listeningOff
          ? 'bg-white/[0.12] text-white/70'
          : 'bg-emerald-300/90 text-slate-900'}"
      >
        <svg viewBox="0 0 24 24" class="mb-0.5 h-5 w-5" fill="none" stroke="currentColor" stroke-width="1.8">
          <rect x="9" y="3" width="6" height="11" rx="3" />
          <path d="M5.5 11a6.5 6.5 0 0 0 13 0M12 17.5V21" stroke-linecap="round" />
          {#if session.listeningOff}<path d="M4 4l16 16" stroke-linecap="round" />{/if}
        </svg>
        {session.listeningOff ? t.micOffShort : t.micOnShort}
      </span>
    </button>
    <p class="-mt-3 mb-4 text-[10px] text-white/35">
      {session.listeningOff ? t.micHintOn : t.micHintOff}
      {#if session.state === "speaking"}
        · <button class="underline decoration-white/30 hover:text-white/70" onclick={() => session.stopVoice()}>{t.stopVoice}</button>
      {/if}
    </p>
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
