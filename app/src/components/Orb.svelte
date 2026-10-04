<script lang="ts">
  import { onMount } from "svelte";
  import { levels } from "../lib/session.svelte";
  import type { EngineState } from "../lib/types";

  let { state, mic }: { state: EngineState; mic: boolean } = $props();

  let canvas: HTMLCanvasElement;

  // Per-state colors (r, g, b) and how "alive" the orb is.
  const PALETTE: Record<EngineState | "waiting", [number, number, number]> = {
    loading: [140, 150, 170],
    idle: [150, 185, 255],
    waiting: [130, 220, 190],
    listening: [255, 140, 110],
    hearing: [255, 196, 92],
    thinking: [178, 140, 255],
    speaking: [96, 205, 255],
  };

  onMount(() => {
    const ctx = canvas.getContext("2d")!;
    let raf = 0;
    let color = [...PALETTE.idle];
    let energy = 0; // smoothed level 0..1
    const bands = new Array(8).fill(0);
    const ripples: { r: number; a: number }[] = [];
    let lastRipple = 0;

    const resize = () => {
      const dpr = window.devicePixelRatio || 1;
      const { width, height } = canvas.getBoundingClientRect();
      canvas.width = Math.round(width * dpr);
      canvas.height = Math.round(height * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    };
    const ro = new ResizeObserver(resize);
    ro.observe(canvas);
    resize();

    const frame = (now: number) => {
      const t = now / 1000;
      const { width: w, height: h } = canvas.getBoundingClientRect();
      ctx.clearRect(0, 0, w, h);
      const cx = w / 2;
      const cy = h / 2;
      const base = Math.min(w, h) * 0.22;

      const key = state === "idle" && mic ? "waiting" : state;
      const target = PALETTE[key];
      color = color.map((c, i) => c + (target[i] - c) * 0.08);
      const rgb = (a: number) => `rgba(${color[0] | 0}, ${color[1] | 0}, ${color[2] | 0}, ${a})`;

      // Live audio drives the orb while listening / speaking.
      const src = state === "speaking" ? levels.out : mic || state === "listening" ? levels.mic : null;
      const fresh = src && now - src.at < 250;
      const rms = fresh ? src!.rms : 0;
      energy += (rms - energy) * (rms > energy ? 0.35 : 0.08);
      for (let i = 0; i < bands.length; i++) {
        const v = fresh ? (src!.bands[i] ?? 0) : 0;
        bands[i] += (v - bands[i]) * 0.25;
      }

      // Glow
      const breath = state === "idle" || state === "loading" ? 0.5 + 0.5 * Math.sin(t * 1.4) : 0;
      const glowR = base * (1.7 + energy * 0.9 + breath * 0.15);
      const g = ctx.createRadialGradient(cx, cy, base * 0.2, cx, cy, glowR);
      g.addColorStop(0, rgb(0.28 + energy * 0.25));
      g.addColorStop(1, rgb(0));
      ctx.fillStyle = g;
      ctx.beginPath();
      ctx.arc(cx, cy, glowR, 0, Math.PI * 2);
      ctx.fill();

      // Ripples while the learner speaks
      if (state === "listening" && energy > 0.35 && now - lastRipple > 260) {
        ripples.push({ r: base, a: 0.35 + energy * 0.3 });
        lastRipple = now;
      }
      for (let i = ripples.length - 1; i >= 0; i--) {
        const rp = ripples[i];
        rp.r += 1.6 + energy * 2;
        rp.a *= 0.965;
        if (rp.a < 0.02) {
          ripples.splice(i, 1);
          continue;
        }
        ctx.strokeStyle = rgb(rp.a);
        ctx.lineWidth = 1.5;
        ctx.beginPath();
        ctx.arc(cx, cy, rp.r, 0, Math.PI * 2);
        ctx.stroke();
      }

      // Body: a blob whose outline follows the spectrum
      const N = 96;
      const amp =
        state === "listening" || state === "speaking"
          ? 0.05 + energy * 0.28
          : state === "loading"
            ? 0.01
            : 0.025 + breath * 0.02;
      ctx.beginPath();
      for (let k = 0; k <= N; k++) {
        const th = (k / N) * Math.PI * 2;
        let d = 0;
        for (let b = 0; b < 4; b++) {
          const level = (bands[b * 2] + bands[b * 2 + 1]) / 2;
          d += (0.35 + level) * Math.sin(th * (b + 2) + t * (0.9 + b * 0.55) * (b % 2 ? 1 : -1));
        }
        if (state === "speaking") d += Math.sin(th * 6 - t * 5) * (0.3 + energy);
        const r = base * (1 + amp * d * 0.5) * (1 + breath * 0.04);
        const x = cx + r * Math.cos(th);
        const y = cy + r * Math.sin(th);
        k ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
      }
      ctx.closePath();
      const body = ctx.createRadialGradient(cx - base * 0.3, cy - base * 0.35, base * 0.1, cx, cy, base * 1.3);
      body.addColorStop(0, rgb(0.95));
      body.addColorStop(0.55, rgb(0.55));
      body.addColorStop(1, rgb(0.18));
      ctx.fillStyle = body;
      ctx.fill();
      ctx.strokeStyle = rgb(0.6);
      ctx.lineWidth = 1;
      ctx.stroke();

      // Hearing: a pulsing arc that sweeps around
      if (state === "hearing") {
        ctx.strokeStyle = rgb(0.8);
        ctx.lineWidth = 3;
        ctx.lineCap = "round";
        const start = t * 4;
        ctx.beginPath();
        ctx.arc(cx, cy, base * 1.25, start, start + Math.PI * (0.6 + 0.3 * Math.sin(t * 6)));
        ctx.stroke();
      }

      // Thinking: orbiting particles (shimmer)
      if (state === "thinking" || state === "loading") {
        const count = state === "thinking" ? 14 : 6;
        for (let i = 0; i < count; i++) {
          const a = t * (state === "thinking" ? 1.6 : 0.6) + (i / count) * Math.PI * 2;
          const rr = base * (1.32 + 0.06 * Math.sin(t * 3 + i));
          const s = 1.5 + 1.5 * (0.5 + 0.5 * Math.sin(t * 5 + i * 1.7));
          ctx.fillStyle = rgb(0.35 + 0.4 * (0.5 + 0.5 * Math.sin(t * 4 + i)));
          ctx.beginPath();
          ctx.arc(cx + rr * Math.cos(a), cy + rr * Math.sin(a), s, 0, Math.PI * 2);
          ctx.fill();
        }
      }

      // Speaking: harmonic rings
      if (state === "speaking") {
        for (let i = 1; i <= 3; i++) {
          ctx.strokeStyle = rgb(0.25 * (1 - i / 4) + energy * 0.2);
          ctx.lineWidth = 1.2;
          ctx.beginPath();
          ctx.arc(cx, cy, base * (1.15 + i * 0.13 + 0.05 * Math.sin(t * 6 + i) * (0.4 + energy)), 0, Math.PI * 2);
          ctx.stroke();
        }
      }

      raf = requestAnimationFrame(frame);
    };
    raf = requestAnimationFrame(frame);
    return () => {
      cancelAnimationFrame(raf);
      ro.disconnect();
    };
  });
</script>

<canvas bind:this={canvas} class="h-full w-full"></canvas>
