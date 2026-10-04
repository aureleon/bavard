<script lang="ts">
  import { onMount } from "svelte";
  import { levels } from "../lib/session.svelte";
  import type { EngineState } from "../lib/types";

  // A rotating wireframe sphere of latitude rings. Each ring is an audio
  // wave: the live spectrum (mic while you speak, voice while the tutor
  // speaks) pushes the rings out, low bands as broad swells and high bands
  // as fine ripples.

  let { state, mic }: { state: EngineState; mic: boolean } = $props();

  let canvas: HTMLCanvasElement;

  const PALETTE: Record<EngineState | "waiting", [number, number, number]> = {
    loading: [140, 150, 170],
    idle: [150, 185, 255],
    waiting: [130, 220, 190],
    listening: [255, 140, 110],
    hearing: [255, 196, 92],
    thinking: [178, 140, 255],
    speaking: [96, 205, 255],
  };

  const RINGS = 26; // latitude rings, poles excluded
  const SEGS = 128; // points per ring
  const BUCKETS = 7; // alpha levels -> one stroke() each

  onMount(() => {
    const ctx = canvas.getContext("2d")!;
    let raf = 0;
    let color = [...PALETTE.idle];
    let energy = 0;
    let spin = 0;
    let last = performance.now();
    const bands = new Array(8).fill(0);

    // Precomputed unit-sphere coordinates.
    const lats = Array.from({ length: RINGS }, (_, i) => -Math.PI / 2 + ((i + 1) / (RINGS + 1)) * Math.PI);
    const cosLon = Array.from({ length: SEGS + 1 }, (_, j) => Math.cos((j / SEGS) * Math.PI * 2));
    const sinLon = Array.from({ length: SEGS + 1 }, (_, j) => Math.sin((j / SEGS) * Math.PI * 2));
    const xs = new Float32Array(SEGS + 1);
    const ys = new Float32Array(SEGS + 1);
    const zs = new Float32Array(SEGS + 1);

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
      const dt = Math.min(0.05, (now - last) / 1000);
      last = now;
      const t = now / 1000;
      const { width: w, height: h } = canvas.getBoundingClientRect();
      ctx.clearRect(0, 0, w, h);
      const cx = w / 2;
      const cy = h / 2;
      const R = Math.min(w, h) * 0.3;

      const key = state === "idle" && mic ? "waiting" : state;
      const target = PALETTE[key];
      color = color.map((c, i) => c + (target[i] - c) * 0.07);
      const rgb = (a: number) => `rgba(${color[0] | 0}, ${color[1] | 0}, ${color[2] | 0}, ${a})`;

      // Live audio
      const src = state === "speaking" ? levels.out : mic || state === "listening" ? levels.mic : null;
      const fresh = src && now - src.at < 250;
      const rms = fresh ? src!.rms : 0;
      energy += (rms - energy) * (rms > energy ? 0.3 : 0.06);
      for (let i = 0; i < 8; i++) {
        const v = fresh ? (src!.bands[i] ?? 0) : 0;
        bands[i] += (v - bands[i]) * 0.22;
      }

      const live = state === "listening" || state === "speaking" || (state === "idle" && mic);
      const breath = 0.5 + 0.5 * Math.sin(t * 1.3);
      // Overall wave height, relative to the radius
      const amp = live
        ? 0.03 + energy * 0.13
        : state === "thinking"
          ? 0.05
          : state === "hearing"
            ? 0.035
            : state === "loading"
              ? 0.012
              : 0.018 + breath * 0.012;

      const spinRate =
        state === "thinking" ? 0.9 : state === "speaking" ? 0.45 : state === "listening" ? 0.35 : state === "loading" ? 0.12 : 0.18;
      spin += spinRate * dt;
      const tilt = 0.42 + 0.06 * Math.sin(t * 0.3);
      const cosT = Math.cos(tilt);
      const sinT = Math.sin(tilt);
      const cosS = Math.cos(spin);
      const sinS = Math.sin(spin);
      const D = 3.2; // camera distance in radii

      // Back glow
      const glowR = R * (1.5 + energy * 0.15);
      const g = ctx.createRadialGradient(cx, cy, R * 0.15, cx, cy, glowR);
      g.addColorStop(0, rgb(0.16 + energy * 0.22));
      g.addColorStop(1, rgb(0));
      ctx.fillStyle = g;
      ctx.beginPath();
      ctx.arc(cx, cy, glowR, 0, Math.PI * 2);
      ctx.fill();

      // Hearing: a bright band scans from pole to pole
      const scan = Math.sin(t * 2.2);

      const paths = Array.from({ length: BUCKETS }, () => new Path2D());

      for (let i = 0; i < RINGS; i++) {
        const lat = lats[i];
        const cl = Math.cos(lat);
        const sl = Math.sin(lat);
        const u = i / (RINGS - 1); // 0 (south) .. 1 (north)
        // Waves are strongest at the equator and fade to the poles.
        const env = cl * cl;
        for (let j = 0; j <= SEGS; j++) {
          const lon = (j / SEGS) * Math.PI * 2;
          let d: number;
          if (live) {
            d =
              (0.4 + bands[0] + bands[1]) * Math.sin(2 * lon + 3 * lat - t * 2.1) +
              (0.3 + bands[2] + bands[3]) * 0.7 * Math.sin(4 * lon - 5 * lat + t * 3.3) +
              (0.2 + bands[4] + bands[5]) * 0.45 * Math.sin(8 * lon + 9 * lat - t * 5.2) +
              (bands[6] + bands[7]) * 0.3 * Math.sin(14 * lon - 13 * lat + t * 7.5);
            if (state === "speaking") d += 0.6 * Math.sin(9 * lat - t * 6) * (0.3 + energy);
            d *= 0.55;
          } else if (state === "thinking") {
            d = Math.sin(3 * lon + 4 * lat - t * 3) + 0.5 * Math.sin(7 * lon - 6 * lat + t * 4.5);
          } else {
            d = Math.sin(2 * lon + 2 * lat - t * 0.8) + 0.4 * Math.sin(5 * lon - 3 * lat + t * 1.1);
          }
          const r = 1 + amp * d * env;
          // Sphere point, then spin around Y and tilt around X.
          const x0 = r * cl * cosLon[j];
          const y0 = r * sl;
          const z0 = r * cl * sinLon[j];
          const x1 = x0 * cosS + z0 * sinS;
          const z1 = -x0 * sinS + z0 * cosS;
          const y2 = y0 * cosT - z1 * sinT;
          const z2 = y0 * sinT + z1 * cosT;
          const f = D / (D - z2);
          xs[j] = cx + x1 * R * f;
          ys[j] = cy - y2 * R * f;
          zs[j] = z2;
        }
        let ringGain = 1;
        if (state === "hearing") {
          const dist = Math.abs(u * 2 - 1 - scan);
          ringGain = 0.35 + 1.4 * Math.max(0, 1 - dist * 2.5);
        }
        for (let j = 0; j < SEGS; j++) {
          const z = (zs[j] + zs[j + 1]) / 2; // -1 back .. 1 front
          const front = Math.min(1, Math.max(0, (z + 1) / 2));
          const depth = 0.08 + 0.92 * Math.pow(front, 1.6);
          const a = Math.min(1, depth * ringGain);
          const b = Math.min(BUCKETS - 1, Math.floor(a * BUCKETS));
          paths[b].moveTo(xs[j], ys[j]);
          paths[b].lineTo(xs[j + 1], ys[j + 1]);
        }
      }

      ctx.lineCap = "round";
      const peak = state === "loading" ? 0.45 : 0.8 + energy * 0.2;
      for (let b = 0; b < BUCKETS; b++) {
        const a = ((b + 0.5) / BUCKETS) * peak;
        ctx.strokeStyle = rgb(a);
        ctx.lineWidth = 0.6 + (b / BUCKETS) * (1.1 + energy * 0.8);
        ctx.stroke(paths[b]);
      }

      // A faint core so the sphere reads as a solid body
      const core = ctx.createRadialGradient(cx - R * 0.25, cy - R * 0.3, R * 0.05, cx, cy, R);
      core.addColorStop(0, rgb(0.1 + energy * 0.12));
      core.addColorStop(1, rgb(0));
      ctx.fillStyle = core;
      ctx.beginPath();
      ctx.arc(cx, cy, R, 0, Math.PI * 2);
      ctx.fill();

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
