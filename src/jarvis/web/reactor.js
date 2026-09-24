/* The arc reactor: JARVIS's face. Draws on a canvas and reacts to what the
   assistant is doing (idle, listening, thinking, speaking, error) and to a
   0..1 energy level (the microphone while listening, speech while talking). */
(() => {
  "use strict";

  const COLORS = {
    idle: [94, 231, 255],
    listening: [94, 231, 255],
    speaking: [150, 243, 255],
    thinking: [255, 195, 90],
    error: [255, 90, 90],
  };
  const TAU = Math.PI * 2;

  function createReactor(canvas) {
    const ctx = canvas.getContext("2d");
    const still = matchMedia("(prefers-reduced-motion: reduce)");
    let mode = "idle";
    let color = COLORS.idle.slice();
    let target = 0.15;          // Energy requested by the app.
    let energy = 0;             // Smoothed energy actually drawn.
    let spin = 0;               // Accumulated rotation.
    let chase = 0;              // Thinking: which coil is lit.
    let bootStart = performance.now();
    let last = bootStart;
    let frame = 0;
    let size = 0;
    let cssW = 0;
    let cssH = 0;
    let drawn = 0;
    const ripples = [];

    function resize() {
      const box = canvas.getBoundingClientRect();
      const ratio = Math.min(window.devicePixelRatio || 1, 2);
      cssW = box.width;
      cssH = box.height;
      size = Math.max(1, Math.min(cssW, cssH));
      canvas.width = Math.round(box.width * ratio);
      canvas.height = Math.round(box.height * ratio);
      ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
      draw(performance.now());
    }

    const rgba = (a) => `rgba(${color[0] | 0},${color[1] | 0},${color[2] | 0},${a})`;

    function ring(r, width, alpha, start = 0, sweep = TAU) {
      ctx.beginPath();
      ctx.arc(0, 0, r, start, start + sweep);
      ctx.lineWidth = width;
      ctx.strokeStyle = rgba(alpha);
      ctx.stroke();
    }

    function draw(now) {
      const dt = Math.min(0.1, (now - last) / 1000);
      last = now;
      const moving = !still.matches;
      const boot = moving ? Math.min(1, (now - bootStart) / 1400) : 1;
      const ease = 1 - Math.pow(1 - boot, 3);

      // Ease colour and energy toward their targets.
      const want = COLORS[mode] || COLORS.idle;
      for (let i = 0; i < 3; i++) color[i] += (want[i] - color[i]) * Math.min(1, dt * 4);
      let goal = target;
      if (mode === "idle") goal = 0.14 + (moving ? 0.05 * Math.sin(now / 900) : 0);
      if (mode === "thinking") goal = 0.45;
      energy += (goal - energy) * Math.min(1, dt * (goal > energy ? 14 : 5));

      const speed = mode === "thinking" ? 1.4 : mode === "listening" ? 0.35 : 0.12;
      if (moving) spin += dt * speed;
      if (mode === "thinking" && moving) chase = (chase + dt * 9) % 10;

      ctx.clearRect(0, 0, cssW, cssH);
      ctx.save();
      ctx.translate(cssW / 2, cssH / 2);
      const R = size / 2 - 4;
      const e = energy;

      // Outer hairline and the rotating tick ring.
      ring(R, 1, 0.22 * ease);
      ctx.save();
      ctx.rotate(spin * 0.4);
      for (let i = 0; i < 72; i++) {
        if (i / 72 > ease) break;
        const long = i % 6 === 0;
        const a = (i / 72) * TAU;
        ctx.beginPath();
        ctx.moveTo(Math.cos(a) * R * (long ? 0.88 : 0.91), Math.sin(a) * R * (long ? 0.88 : 0.91));
        ctx.lineTo(Math.cos(a) * R * 0.95, Math.sin(a) * R * 0.95);
        ctx.lineWidth = long ? 2 : 1;
        ctx.strokeStyle = rgba(long ? 0.7 : 0.35);
        ctx.stroke();
      }
      ctx.restore();

      // Segmented arcs turning the other way.
      ctx.save();
      ctx.rotate(-spin);
      const arcs = [[0, 1.25], [1.6, 0.7], [2.6, 1.9], [4.8, 0.55]];
      for (const [start, sweep] of arcs) ring(R * 0.8, 3, 0.75 * ease, start, sweep * ease);
      ctx.restore();
      ring(R * 0.73, 1, 0.3 * ease);

      // Thinking: a radar sweep chasing round the rim.
      if (mode === "thinking") {
        const head = spin * 2.4;
        for (let i = 0; i < 18; i++) {
          ring(R * 0.86, 5, 0.5 * (1 - i / 18), head - i * 0.05, 0.05);
        }
      }

      // Ten coils, like the reactor in the cave.
      const inner = R * 0.42;
      const outer = R * 0.64;
      for (let i = 0; i < 10; i++) {
        const a0 = (i / 10) * TAU - Math.PI / 2 + 0.05;
        const a1 = a0 + TAU / 10 - 0.1;
        let lit = 0.18 + e * 0.65;
        if (mode === "thinking") {
          const d = Math.min(Math.abs(i - chase), 10 - Math.abs(i - chase));
          lit = 0.15 + 0.75 * Math.max(0, 1 - d / 2.5);
        }
        lit *= ease;
        ctx.beginPath();
        ctx.arc(0, 0, outer, a0, a1);
        ctx.arc(0, 0, inner, a1 - 0.02, a0 + 0.02, true);
        ctx.closePath();
        ctx.fillStyle = rgba(lit * 0.55);
        ctx.fill();
        ctx.lineWidth = 1.5;
        ctx.strokeStyle = rgba(Math.min(1, lit + 0.2));
        ctx.stroke();
      }
      ring(inner * 0.9, 2, 0.8 * ease);

      // Listening: ripples leave the core in time with your voice.
      if (mode === "listening" && moving && Math.random() < dt * (2 + e * 10)) {
        ripples.push({ r: inner * 0.9, a: 0.35 + e * 0.5 });
      }
      for (let i = ripples.length - 1; i >= 0; i--) {
        const rip = ripples[i];
        rip.r += dt * R * 0.6;
        rip.a -= dt * 0.7;
        if (rip.a <= 0 || rip.r > R) { ripples.splice(i, 1); continue; }
        ring(rip.r, 1.5, rip.a);
      }

      // The core.
      const core = inner * (0.72 + e * 0.28) * (0.4 + 0.6 * ease);
      const glow = ctx.createRadialGradient(0, 0, 0, 0, 0, core * 1.9);
      glow.addColorStop(0, `rgba(255,255,255,${0.95 * ease})`);
      glow.addColorStop(0.28, rgba(0.9 * ease));
      glow.addColorStop(0.55, rgba(0.35 * ease * (0.5 + e)));
      glow.addColorStop(1, rgba(0));
      ctx.fillStyle = glow;
      ctx.beginPath();
      ctx.arc(0, 0, core * 1.9, 0, TAU);
      ctx.fill();

      ctx.restore();
    }

    function loop(now) {
      frame = 0;
      // At rest the reactor only breathes, so 30 frames a second is plenty for
      // a window that may stay open all day; reduced motion needs fewer still.
      const gap = still.matches ? 250 : mode === "idle" ? 33 : 0;
      if (now - drawn >= gap) { drawn = now; draw(now); }
      if (!document.hidden) frame = requestAnimationFrame(loop);
    }

    function wake() {
      if (!frame) { last = performance.now(); frame = requestAnimationFrame(loop); }
    }

    new ResizeObserver(resize).observe(canvas);
    document.addEventListener("visibilitychange", () => { if (!document.hidden) wake(); });
    still.addEventListener?.("change", wake);
    resize();
    wake();

    return {
      setMode(next) { mode = next; if (next !== "listening") ripples.length = 0; wake(); },
      setLevel(value) { target = Math.max(0, Math.min(1, value)); },
      reboot() { bootStart = performance.now(); wake(); },
    };
  }

  window.createReactor = createReactor;
})();
