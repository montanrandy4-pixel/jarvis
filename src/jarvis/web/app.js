/* JARVIS app front end: the HUD, streaming replies, speech in and out. */
(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const all = (selector) => document.querySelectorAll(selector);
  const el = {
    orb: $("orb"), caption: $("orb-caption"), transcript: $("transcript"),
    composer: $("composer"), input: $("input"), mic: $("mic"), send: $("send"),
    statusDot: $("status-dot"), statusText: $("status-text"),
    panel: $("panel"), panelToggle: $("panel-toggle"),
    panelModel: $("panel-model"), panelDetail: $("panel-detail"),
    panelTools: $("panel-tools"),
    voiceToggle: $("voice-toggle"), wakeToggle: $("wake-toggle"),
    wakeWord: $("wake-word"), voiceSupport: $("voice-support"),
    confirmSlot: $("confirm-slot"), reset: $("reset"), name: $("assistant-name"),
    install: $("install"), shutdown: $("shutdown"),
    clockSec: $("clock-sec"), clockDate: $("clock-date"),
  };

  const reactor = window.createReactor($("reactor"));

  const state = {
    busy: false,
    offline: false,
    booted: false,
    speakReplies: load("speak", true),
    wakeListening: load("wake", false),
    wakeWord: "hey jarvis",
    addressAs: "sir",
    bubble: null,
    toolRow: null,
    timers: [],
    kick: 0,              // A burst of reactor energy when a word is spoken.
  };

  function load(key, fallback) {
    try {
      const raw = localStorage.getItem("jarvis." + key);
      return raw === null ? fallback : JSON.parse(raw);
    } catch { return fallback; }
  }
  function save(key, value) {
    try { localStorage.setItem("jarvis." + key, JSON.stringify(value)); } catch {}
  }

  function post(path, payload = {}) {
    return fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  }

  /* ---------- reactor + status ---------- */

  const CAPTIONS = {
    idle: "Standing by",
    listening: "Listening",
    thinking: "Processing",
    speaking: "Speaking",
    error: "Attention required",
  };

  function setOrb(mode, caption) {
    el.orb.dataset.state = mode;
    el.caption.dataset.state = mode;
    reactor.setMode(mode);
    const live = caption !== undefined && caption !== CAPTIONS[mode];
    el.caption.toggleAttribute("data-live", Boolean(live && caption));
    el.caption.textContent = caption ?? CAPTIONS[mode] ?? "";
  }

  function setBusy(busy) {
    state.busy = busy;
    el.send.disabled = busy || state.offline;
    el.input.disabled = busy || state.offline;
    el.input.placeholder = busy ? "JARVIS is working…" : "Speak or type a command…";
    if (!busy && !state.offline) el.input.focus();
  }

  /* ---------- the log ---------- */

  function stamp() {
    return new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  }

  function addEntry(who, label) {
    const item = document.createElement("li");
    item.className = `entry ${who}`;
    const meta = document.createElement("p");
    meta.className = "meta";
    const name = document.createElement("span");
    name.textContent = label;
    const time = document.createElement("time");
    time.textContent = stamp();
    meta.append(name, time);
    const text = document.createElement("p");
    text.className = "text";
    item.append(meta, text);
    el.transcript.append(item);
    return text;
  }

  function addTurn(who, text, { error = false } = {}) {
    const body = addEntry(who, who === "user" ? "You" : el.name.textContent);
    body.textContent = text;
    if (error) body.classList.add("error");
    scrollDown();
    return body;
  }

  /* A system line: parts are strings, or [text, "ok" | "warn"] to highlight. */
  function addSystem(...parts) {
    const body = addEntry("system", "System");
    for (const part of parts) {
      if (Array.isArray(part)) {
        const b = document.createElement("b");
        b.textContent = part[0];
        if (part[1] === "warn") b.className = "warn";
        body.append(b);
      } else {
        body.append(part);
      }
    }
    scrollDown();
  }

  function scrollDown() {
    el.transcript.scrollTop = el.transcript.scrollHeight;
  }

  function addTool(label, failed) {
    if (!state.toolRow) {
      state.toolRow = document.createElement("div");
      state.toolRow.className = "tools";
      (state.bubble?.parentElement ?? el.transcript).append(state.toolRow);
    }
    const chip = document.createElement("span");
    chip.className = "tool" + (failed ? " failed" : "");
    chip.textContent = label;
    state.toolRow.append(chip);
    scrollDown();
  }

  /* ---------- speaking ---------- */

  const synth = window.speechSynthesis;
  const speech = {
    buffer: "",
    // Release complete sentences so speech starts before the reply is finished.
    feed(chunk) {
      if (!state.speakReplies || !synth) return;
      this.buffer += chunk;
      let match;
      const boundary = /([.!?…]["')\]]?)(\s+)/;
      while ((match = boundary.exec(this.buffer))) {
        const end = match.index + match[1].length;
        this.utter(this.buffer.slice(0, end));
        this.buffer = this.buffer.slice(match.index + match[0].length);
      }
    },
    flush() {
      const tail = this.buffer.trim();
      this.buffer = "";
      if (tail) this.utter(tail);
    },
    utter(text) {
      if (!state.speakReplies || !synth || !text.trim()) return;
      const utterance = new SpeechSynthesisUtterance(text.trim());
      utterance.rate = 1.03;
      utterance.pitch = 0.95;
      const voice = pickVoice();
      if (voice) utterance.voice = voice;
      utterance.onboundary = () => { state.kick = 1; };
      utterance.onend = () => {
        if (!synth.speaking && !state.busy) setOrb("idle");
      };
      synth.speak(utterance);
    },
    stop() {
      this.buffer = "";
      if (synth) synth.cancel();
    },
  };

  let cachedVoice;
  function pickVoice() {
    if (cachedVoice !== undefined) return cachedVoice;
    const voices = synth ? synth.getVoices() : [];
    if (!voices.length) return null;           // Not loaded yet; try next time.
    const wanted = [/daniel/i, /google uk english male/i, /ryan/i, /arthur/i, /en-GB/i];
    cachedVoice =
      wanted.map((re) => voices.find((v) => re.test(v.name) || re.test(v.lang)))
            .find(Boolean) || voices.find((v) => /^en/i.test(v.lang)) || null;
    return cachedVoice;
  }
  if (synth) synth.onvoiceschanged = () => { cachedVoice = undefined; };

  /* ---------- reactor energy: your voice in, its voice out ---------- */

  const meter = {
    stream: null, context: null, analyser: null, data: null,
    async start() {
      if (this.stream || !navigator.mediaDevices?.getUserMedia) return;
      try {
        this.stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        this.context = new AudioContext();
        this.analyser = this.context.createAnalyser();
        this.analyser.fftSize = 512;
        this.data = new Uint8Array(this.analyser.fftSize);
        this.context.createMediaStreamSource(this.stream).connect(this.analyser);
      } catch { this.stop(); }
    },
    level() {
      if (!this.analyser) return null;
      this.analyser.getByteTimeDomainData(this.data);
      let sum = 0;
      for (const v of this.data) { const x = (v - 128) / 128; sum += x * x; }
      return Math.min(1, Math.sqrt(sum / this.data.length) * 4.5);
    },
    stop() {
      this.stream?.getTracks().forEach((t) => t.stop());
      this.context?.close().catch(() => {});
      this.stream = this.context = this.analyser = null;
    },
  };

  function pumpEnergy(now) {
    const mode = el.orb.dataset.state;
    if (mode === "listening") {
      const heard = meter.level();
      reactor.setLevel(heard ?? 0.3 + 0.15 * Math.sin(now / 180));
    } else if (synth?.speaking) {
      state.kick *= 0.9;
      const talk = 0.45 + 0.2 * Math.sin(now / 75) * Math.sin(now / 310);
      reactor.setLevel(Math.max(talk, state.kick));
    } else {
      reactor.setLevel(0.15);
    }
    requestAnimationFrame(pumpEnergy);
  }
  requestAnimationFrame(pumpEnergy);

  /* ---------- talking to the server ---------- */

  async function send(text) {
    if (state.busy || state.offline || !text.trim()) return;
    setBusy(true);
    speech.stop();
    addTurn("user", text);
    state.bubble = addTurn("jarvis", "");
    state.bubble.classList.add("streaming");
    state.toolRow = null;
    setOrb("thinking");

    let response;
    try {
      response = await post("/api/chat", { text });
    } catch {
      return finish({ error: "I lost the connection to the app." });
    }
    if (!response.ok || !response.body) {
      const detail = await response.json().catch(() => ({}));
      return finish({ error: detail.error || `Request failed (${response.status}).` });
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let pending = "";
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      pending += decoder.decode(value, { stream: true });
      const parts = pending.split("\n\n");
      pending = parts.pop();
      for (const part of parts) {
        const line = part.trim();
        if (!line.startsWith("data:")) continue;
        let event;
        try { event = JSON.parse(line.slice(5)); } catch { continue; }
        handle(event);
      }
    }
    if (state.busy) finish({});
  }

  function handle(event) {
    switch (event.type) {
      case "text":
        state.bubble.textContent += event.value;
        speech.feed(event.value);
        if (el.orb.dataset.state !== "speaking") setOrb("speaking");
        scrollDown();
        break;
      case "tool":
        addTool(event.label, event.error);
        refreshTelemetry();
        break;
      case "confirm":
        askPermission(event);
        break;
      case "announce":
        addTurn("jarvis", event.text);
        speech.utter(event.text);
        refreshTelemetry();
        break;
      case "done":
        finish(event);
        break;
    }
  }

  function finish(event) {
    setBusy(false);
    state.bubble?.classList.remove("streaming");
    speech.flush();
    if (event.error) {
      if (state.bubble && !state.bubble.textContent) {
        state.bubble.classList.add("error");
        state.bubble.textContent = event.error;
      } else {
        addTurn("jarvis", event.error, { error: true });
      }
      speech.utter(event.error);
      setOrb("error");
    } else if (state.bubble && !state.bubble.textContent) {
      state.bubble.textContent = event.cancelled ? "(stopped)" : "(no reply)";
    }
    if (!event.error) setOrb(synth?.speaking ? "speaking" : "idle");
    refreshState();
    if (state.wakeListening) listenForWake();
  }

  function askPermission(event) {
    el.confirmSlot.hidden = false;
    el.confirmSlot.innerHTML = "";
    const card = document.createElement("div");
    card.className = "confirm";
    const question = document.createElement("p");
    const heading = document.createElement("strong");
    heading.textContent = "Permission required";
    question.append(heading, `Shall I ${event.summary}?`);
    const allow = document.createElement("button");
    allow.className = "allow";
    allow.textContent = "Allow";
    const deny = document.createElement("button");
    deny.className = "deny";
    deny.textContent = "No";
    card.append(question, allow, deny);
    el.confirmSlot.append(card);
    speech.utter(`Shall I ${event.summary}?`);

    const answer = (allowed) => {
      el.confirmSlot.hidden = true;
      el.confirmSlot.innerHTML = "";
      post("/api/confirm", { id: event.id, allow: allowed }).catch(() => {});
    };
    allow.onclick = () => answer(true);
    deny.onclick = () => answer(false);
    allow.focus();
  }

  /* ---------- listening ---------- */

  const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  let recogniser = null;
  let mode = "off";           // off | wake | command

  function startRecognition(next) {
    if (!Recognition) return false;
    stopRecognition();
    mode = next;
    recogniser = new Recognition();
    recogniser.lang = "en-GB";
    recogniser.interimResults = mode === "command";
    recogniser.continuous = mode === "wake";
    recogniser.onresult = (event) => {
      const result = event.results[event.results.length - 1];
      const said = result[0].transcript.trim();
      if (mode === "wake") {
        const heard = said.toLowerCase().replace(/[^a-z\s]/g, "");
        const phrase = state.wakeWord.toLowerCase();
        if (heard.includes(phrase.replace(/[^a-z\s]/g, ""))) {
          const rest = said.slice(said.toLowerCase().indexOf(phrase) + phrase.length);
          stopRecognition();
          if (rest.replace(/[^a-z]/gi, "").length > 2) send(rest.replace(/^[\s,.]+/, ""));
          else listenForCommand();
        }
        return;
      }
      if (result.isFinal) {
        stopRecognition();
        setMic(false);
        send(said);
      } else {
        setOrb("listening", said || CAPTIONS.listening);
      }
    };
    recogniser.onerror = (event) => {
      if (event.error === "not-allowed") {
        el.voiceSupport.textContent = "Microphone permission was denied.";
        state.wakeListening = false;
        el.wakeToggle.checked = false;
      }
      stopRecognition();
      setMic(false);
    };
    recogniser.onend = () => {
      // Continuous recognition stops itself after a while; restart it.
      if (mode === "wake" && state.wakeListening && !state.busy) {
        setTimeout(() => listenForWake(), 300);
      } else if (mode === "command") {
        setMic(false);
      }
    };
    try { recogniser.start(); } catch { return false; }
    return true;
  }

  function stopRecognition() {
    const current = recogniser;
    recogniser = null;
    mode = "off";
    meter.stop();
    if (current) { try { current.abort(); } catch {} }
  }

  function listenForWake() {
    if (!state.wakeListening || state.busy) return;
    startRecognition("wake");
  }

  function listenForCommand() {
    speech.stop();
    if (startRecognition("command")) {
      meter.start();
      setMic(true);
      setOrb("listening");
    }
  }

  function setMic(on) {
    el.mic.setAttribute("aria-pressed", String(on));
    if (!on && !state.busy && !synth?.speaking) setOrb("idle");
  }

  /* ---------- things that happen between turns ---------- */

  async function pollAnnouncements() {
    // A long poll, so a timer going off reaches you without a page refresh.
    while (!state.offline) {
      try {
        const response = await fetch("/api/events");
        const data = await response.json();
        for (const event of data.events || []) handle(event);
      } catch {
        await new Promise((resume) => setTimeout(resume, 3000));
      }
    }
  }

  /* ---------- telemetry ---------- */

  function setMetric(name, text) {
    for (const node of all(`[data-metric="${name}"]`)) node.textContent = text;
  }

  function setGauge(name, pct, text, note, level) {
    setMetric(name, text);
    const gauge = document.querySelector(`[data-gauge="${name}"]`);
    if (gauge) {
      gauge.querySelector(".g-bar i").style.setProperty("--pct", `${pct ?? 0}%`);
      gauge.dataset.level = level ?? (pct >= 95 ? "critical" : pct >= 85 ? "high" : "normal");
    }
    const noteEl = document.querySelector(`[data-note="${name}"]`);
    if (noteEl) noteEl.textContent = note || " ";
  }

  function uptime(seconds) {
    if (seconds == null) return "—";
    const d = Math.floor(seconds / 86400);
    const h = Math.floor((seconds % 86400) / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    return d ? `${d}d ${h}h` : h ? `${h}h ${m}m` : `${m}m`;
  }

  async function refreshTelemetry() {
    let data;
    try {
      data = await (await fetch("/api/telemetry")).json();
    } catch { return; }
    const pct = (v) => (v == null ? "—" : `${Math.round(v)}%`);

    setGauge("cpu", data.cpu_pct, pct(data.cpu_pct),
      data.cpus ? `${data.cpus} cores` : "");
    const mem = data.memory;
    setGauge("memory", mem?.used_pct, pct(mem?.used_pct),
      mem ? `${mem.available_gb} of ${mem.total_gb} GB free` : "unavailable");
    const disk = data.disk;
    setGauge("disk", disk?.used_pct, pct(disk?.used_pct),
      disk ? `${disk.free_gb} GB free` : "unavailable");
    const bat = data.battery;
    if (bat) {
      const charging = /charg|full/.test(bat.status) && !/discharg/.test(bat.status);
      const level = charging ? "normal" : bat.pct <= 10 ? "critical" : bat.pct <= 20 ? "high" : "normal";
      setGauge("battery", bat.pct, `${bat.pct}%`, bat.status, level);
    } else {
      setGauge("battery", 100, "AC", "mains power", "normal");
    }
    setMetric("host", data.host || "—");
    setMetric("uptime", uptime(data.uptime_s));

    const now = Date.now();
    state.timers = (data.timers || []).map((t) => ({
      ...t, ends: now + t.remaining_s * 1000,
    }));
    renderTimers();
  }

  const clockFormat = new Intl.DateTimeFormat([], { hour: "2-digit", minute: "2-digit" });

  function clockTick() {
    const now = new Date();
    // Big hours and minutes; seconds and any AM/PM ride small beside them.
    const parts = clockFormat.formatToParts(now);
    const period = parts.find((p) => p.type === "dayPeriod")?.value ?? "";
    const main = parts.filter((p) => p.type !== "dayPeriod").map((p) => p.value).join("").trim();
    setMetric("clock", main);
    el.clockSec.textContent = ":" + String(now.getSeconds()).padStart(2, "0") + (period ? " " + period : "");
    el.clockDate.textContent = now.toLocaleDateString([], {
      weekday: "short", day: "numeric", month: "short", year: "numeric",
    });
    if (state.timers.length) renderTimers();
  }

  function renderTimers() {
    const now = Date.now();
    for (const list of all('[data-list="timers"]')) {
      list.innerHTML = "";
      const live = state.timers.filter((t) => t.ends > now - 1000);
      if (!live.length) {
        const empty = document.createElement("li");
        empty.className = "empty";
        empty.textContent = "None running. Try “set a timer for ten minutes”.";
        list.append(empty);
        continue;
      }
      for (const timer of live) {
        const left = Math.max(0, Math.round((timer.ends - now) / 1000));
        const li = document.createElement("li");
        const head = document.createElement("div");
        head.className = "t-head";
        const label = document.createElement("span");
        label.textContent = timer.label || `Timer ${timer.id}`;
        const count = document.createElement("span");
        count.className = "t-left";
        const h = Math.floor(left / 3600);
        const m = Math.floor((left % 3600) / 60);
        const s = String(left % 60).padStart(2, "0");
        count.textContent = h ? `${h}:${String(m).padStart(2, "0")}:${s}` : `${m}:${s}`;
        head.append(label, count);
        const bar = document.createElement("div");
        bar.className = "t-bar";
        const fill = document.createElement("i");
        fill.style.width = `${timer.duration_s ? (100 * left) / timer.duration_s : 0}%`;
        bar.append(fill);
        li.append(head, bar);
        list.append(li);
      }
    }
  }

  /* ---------- state ---------- */

  async function refreshState() {
    let data;
    try {
      data = await (await fetch("/api/state")).json();
    } catch {
      el.statusDot.dataset.state = "error";
      el.statusText.textContent = "App not responding";
      return null;
    }
    el.name.textContent = data.name || "JARVIS";
    document.title = el.name.textContent;
    state.wakeWord = data.wake_word || "hey jarvis";
    state.addressAs = data.user_name || data.address_as || "";
    el.wakeWord.textContent = state.wakeWord;
    el.statusDot.dataset.state = data.ready ? "ready" : "error";
    el.statusText.textContent = data.ready ? `Online · ${data.model}` : "Model offline";
    el.statusText.title = data.detail || "";
    setMetric("model", data.model || "—");
    el.panelModel.textContent = `${data.model} via ${data.backend}`;
    el.panelDetail.textContent = data.detail;
    el.panelTools.innerHTML = "";
    for (const tool of data.tools || []) {
      const li = document.createElement("li");
      li.textContent = tool;
      el.panelTools.append(li);
    }
    renderMemory(data.memories || []);
    return data;
  }

  function renderMemory(memories) {
    for (const list of all('[data-list="memory"]')) {
      list.innerHTML = "";
      if (!memories.length) {
        const empty = document.createElement("li");
        empty.className = "empty";
        empty.textContent = "Nothing stored yet. Tell me something worth remembering.";
        list.append(empty);
        continue;
      }
      for (const item of memories) {
        const li = document.createElement("li");
        const text = document.createElement("span");
        text.textContent = item.text;
        const remove = document.createElement("button");
        remove.type = "button";
        remove.textContent = "×";
        remove.title = "Forget this";
        remove.setAttribute("aria-label", `Forget: ${item.text}`);
        remove.onclick = async () => {
          await post("/api/forget", { id: item.id }).catch(() => {});
          refreshState();
        };
        li.append(text, remove);
        list.append(li);
      }
    }
  }

  /* ---------- start-up ---------- */

  function greeting() {
    const hour = new Date().getHours();
    const part = hour < 5 ? "evening" : hour < 12 ? "morning" : hour < 18 ? "afternoon" : "evening";
    const who = state.addressAs ? `, ${state.addressAs}` : "";
    return `Good ${part}${who}.`;
  }

  async function boot() {
    const data = await refreshState();
    if (state.booted) return;
    state.booted = true;
    const pause = (ms) => new Promise((resume) => setTimeout(resume, ms));
    if (!data) {
      addSystem("Cannot reach the JARVIS app. ", ["Is it still running?", "warn"]);
      return;
    }
    addSystem("Arc reactor ", ["online", "ok"], ` · ${data.tools.length} tools loaded`);
    await pause(160);
    if (data.ready) {
      addSystem(`Language model ${data.model} `, ["ready", "ok"]);
    } else {
      addSystem(`Language model ${data.model} `, ["offline", "warn"], ` · ${data.detail}`);
    }
    await pause(160);
    const count = data.memories.length;
    addSystem(`Memory core · ${count} ${count === 1 ? "entry" : "entries"}`);
    await pause(240);
    addTurn("jarvis", data.ready
      ? `${greeting()} All systems are online. How can I help?`
      : `${greeting()} My language model is offline, so I can't answer yet. The line above says how to fix it.`);
  }

  /* ---------- wiring ---------- */

  el.composer.addEventListener("submit", (event) => {
    event.preventDefault();
    const text = el.input.value;
    el.input.value = "";
    send(text);
  });

  el.mic.addEventListener("click", () => {
    if (mode === "command") { stopRecognition(); setMic(false); return; }
    listenForCommand();
  });

  el.orb.addEventListener("click", () => {
    if (state.busy || synth?.speaking) {
      // Tapping during a reply is how you interrupt it.
      speech.stop();
      post("/api/cancel").catch(() => {});
      setOrb("idle");
      return;
    }
    if (mode === "command") { stopRecognition(); setMic(false); return; }
    listenForCommand();
  });

  el.voiceToggle.addEventListener("click", () => {
    state.speakReplies = !state.speakReplies;
    save("speak", state.speakReplies);
    el.voiceToggle.setAttribute("aria-pressed", String(state.speakReplies));
    el.voiceToggle.textContent = state.speakReplies ? "Voice on" : "Voice off";
    if (!state.speakReplies) speech.stop();
  });

  el.wakeToggle.addEventListener("change", () => {
    state.wakeListening = el.wakeToggle.checked;
    save("wake", state.wakeListening);
    if (state.wakeListening) listenForWake();
    else stopRecognition();
  });

  el.panelToggle.addEventListener("click", () => {
    const open = el.panel.hidden;
    el.panel.hidden = !open;
    el.panelToggle.setAttribute("aria-expanded", String(open));
    if (open) refreshState();
  });

  el.reset.addEventListener("click", async () => {
    await post("/api/reset").catch(() => {});
    el.transcript.innerHTML = "";
    speech.stop();
    setOrb("idle");
  });

  let shutdownArmed = null;
  el.shutdown.addEventListener("click", async () => {
    if (!shutdownArmed) {
      el.shutdown.textContent = "Tap again to shut down";
      shutdownArmed = setTimeout(() => {
        shutdownArmed = null;
        el.shutdown.textContent = "Shut down JARVIS";
      }, 4000);
      return;
    }
    clearTimeout(shutdownArmed);
    await post("/api/shutdown").catch(() => {});
    state.offline = true;
    stopRecognition();
    speech.stop();
    setBusy(false);
    el.panelToggle.click();
    el.statusDot.dataset.state = "error";
    el.statusText.textContent = "Shut down";
    setOrb("error", "Offline");
    addSystem("JARVIS has shut down. Close this window, or open JARVIS again to restart it.");
  });

  // Chrome and Edge offer to install the app; surface that as a button.
  let installPrompt = null;
  window.addEventListener("beforeinstallprompt", (event) => {
    event.preventDefault();
    installPrompt = event;
    el.install.hidden = false;
  });
  el.install.addEventListener("click", async () => {
    if (!installPrompt) return;
    installPrompt.prompt();
    await installPrompt.userChoice.catch(() => {});
    installPrompt = null;
    el.install.hidden = true;
  });
  window.addEventListener("appinstalled", () => { el.install.hidden = true; });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      speech.stop();
      if (state.busy) post("/api/cancel").catch(() => {});
      if (!el.panel.hidden) el.panelToggle.click();
    }
  });

  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) refreshTelemetry();
  });

  /* ---------- start ---------- */

  el.voiceToggle.setAttribute("aria-pressed", String(state.speakReplies));
  el.voiceToggle.textContent = state.speakReplies ? "Voice on" : "Voice off";
  el.wakeToggle.checked = state.wakeListening;
  if (!Recognition) {
    el.mic.disabled = true;
    el.wakeToggle.disabled = true;
    el.voiceSupport.textContent =
      "This browser cannot listen. Chrome or Edge can; typing works everywhere.";
  } else {
    el.voiceSupport.textContent =
      "Speech recognition is provided by the browser and may use an online service.";
  }
  if (!synth) el.voiceToggle.disabled = true;
  setOrb("idle");
  clockTick();
  setInterval(clockTick, 1000);
  refreshTelemetry();
  setInterval(() => { if (!document.hidden && !state.offline) refreshTelemetry(); }, 5000);
  boot();
  pollAnnouncements();
  if (location.hash === "#listen") listenForCommand();
  else if (state.wakeListening) listenForWake();
  el.input.focus();
})();
