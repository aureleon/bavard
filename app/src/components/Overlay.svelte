<script lang="ts">
  import { session } from "../lib/session.svelte";

  const t = $derived(session.t);

  const gb = (n: number) => (n / 1e9).toFixed(2);
  const downloads = $derived(Object.entries(session.downloads).filter(([, d]) => !d.finished));
</script>

{#if session.phase !== "ready"}
  <div class="absolute inset-x-0 top-12 bottom-0 z-20 grid place-items-center bg-black/35 backdrop-blur-sm">
    <div class="w-[min(520px,85%)] rounded-2xl bg-neutral-900/80 p-6 text-white/85 shadow-2xl ring-1 ring-white/10">
      {#if session.phase === "crashed"}
        <h2 class="mb-2 text-base font-semibold text-rose-300">
          {session.crash?.setup ? t.setupFailed : t.crashTitle}
        </h2>
        <pre
          class="mb-4 max-h-56 overflow-auto rounded-lg bg-black/50 p-3 text-[11px] leading-snug whitespace-pre-wrap text-white/70 select-text">{session
            .crash?.tail || "—"}</pre>
        <div class="flex gap-2">
          <button class="rounded-lg bg-white px-3 py-1.5 text-sm font-medium text-slate-900 hover:bg-white/90" onclick={() => session.restart()}>
            {t.restart}
          </button>
          <button class="rounded-lg bg-white/10 px-3 py-1.5 text-sm hover:bg-white/20" onclick={() => session.bridge.openLog()}>
            {t.openLog}
          </button>
        </div>
      {:else if session.phase === "setup"}
        <h2 class="mb-1 text-base font-semibold">{t.setup.title}</h2>
        <p class="mb-3 text-sm text-white/60">{t.setup[session.setupStage ?? ""] ?? session.setupStage}</p>
        <pre class="h-40 overflow-hidden rounded-lg bg-black/50 p-3 text-[10px] leading-snug text-white/50">{session.setupLines
            .slice(-12)
            .join("\n")}</pre>
      {:else}
        <div class="flex items-center gap-3">
          <span class="spinner"></span>
          <p class="text-sm">{t.loadingStages[session.loadingStage] ?? session.loadingStage}</p>
        </div>
        {#each downloads as [repo, d] (repo)}
          <div class="mt-4">
            <div class="mb-1 flex justify-between text-[11px] text-white/55">
              <span>{t.download} {repo}</span>
              <span class="tabular-nums">{gb(d.done)}{d.total ? ` / ${gb(d.total)}` : ""} GB</span>
            </div>
            <div class="h-1.5 overflow-hidden rounded-full bg-white/10">
              <div
                class="h-full rounded-full bg-sky-300 transition-[width]"
                style:width={d.total ? `${Math.min(100, (100 * d.done) / d.total)}%` : "30%"}
              ></div>
            </div>
          </div>
        {/each}
      {/if}
    </div>
  </div>
{/if}

<style>
  .spinner {
    width: 16px;
    height: 16px;
    border-radius: 50%;
    border: 2px solid rgb(255 255 255 / 0.2);
    border-top-color: rgb(125 211 252);
    animation: spin 0.8s linear infinite;
  }
  @keyframes spin {
    to {
      transform: rotate(360deg);
    }
  }
</style>
