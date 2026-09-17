/* JARVIS app front end: streaming replies, speech in and out. */
(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const el = {
    orb: $("orb"), caption: $("orb-caption"), transcript: $("transcript"),
    composer: $("composer"), input: $("input"), mic: $("mic"), send: $("send"),
    statusDot: $("status-dot"), statusText: $("status-text"),
    panel: $("panel"), panelToggle: $("panel-toggle"),
    panelModel: $("panel-model"), panelDetail: $("panel-detail"),
    panelTools: $("panel-tools"), panelMemory: $("panel-memory"),
    voiceToggle: $("voice-toggle"), wakeToggle: $("wake-toggle"),
    wakeWord: $("wake-word"), voiceSupport: $("voice-support"),
    confirmSlot: $("confirm-slot"), reset: $("reset"), name: $("assistant-name"),
  };

  const state = {
    busy: false,
    speakReplies: load("speak", true),
    wakeListening: load("wake", false),
    wakeWord: "hey jarvis",
    bubble: null,
    toolRow: null,
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

  /* ---------- orb + status ---------- */

  const CAPTIONS = {
    idle: "Tap the orb, or just type.",
    listening: "Listening…",
    thinking: "Thinking…",
    speaking: "",
    error: "Something needs attention.",
  };

  function setOrb(mode, caption) {
    el.orb.dataset.state = mode;
    el.caption.textContent = caption ?? CAPTIONS[mode] ?? "";
  }

  function setBusy(busy) {
    state.busy = busy;
    el.send.disabled = busy;
    el.input.disabled = busy;
    el.input.placeholder = busy ? "JARVIS is working…" : "Ask JARVIS…";
    if (!busy) el.input.focus();
  }

  /* ---------- transcript ---------- */

  function addTurn(who, text, { error = false } = {}) {
    const item = document.createElement("li");
    item.className = `turn ${who}`;
    const label = document.createElement("p");
    label.className = "who";
    label.textContent = who === "user" ? "You" : el.name.textContent;
    const bubble = document.createElement("div");
    bubble.className = "bubble" + (error ? " error" : "");
    bubble.textContent = text;
    item.append(label, bubble);
    el.transcript.append(item);
    scrollDown();
    return bubble;
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
    const wanted = [/daniel/i, /google uk english male/i, /arthur/i, /en-GB/i];
    cachedVoice =
      wanted.map((re) => voices.find((v) => re.test(v.name) || re.test(v.lang)))
            .find(Boolean) || voices.find((v) => /^en/i.test(v.lang)) || null;
    return cachedVoice;
  }
  if (synth) synth.onvoiceschanged = () => { cachedVoice = undefined; };

  /* ---------- talking to the server ---------- */

  async function send(text) {
    if (state.busy || !text.trim()) return;
    setBusy(true);
    speech.stop();
    addTurn("user", text);
    state.bubble = addTurn("jarvis", "");
    state.bubble.classList.add("streaming");
    state.toolRow = null;
    setOrb("thinking");

    let response;
    try {
      response = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text }),
      });
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
        if (el.orb.dataset.state !== "speaking") setOrb("speaking", "");
        scrollDown();
        break;
      case "tool":
        addTool(event.label, event.error);
        break;
      case "confirm":
        askPermission(event);
        break;
      case "announce":
        addTurn("jarvis", event.text);
        speech.utter(event.text);
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
    question.innerHTML = `<strong>Permission needed.</strong> Shall I ${escapeHtml(event.summary)}?`;
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
      fetch("/api/confirm", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id: event.id, allow: allowed }),
      }).catch(() => {});
    };
    allow.onclick = () => answer(true);
    deny.onclick = () => answer(false);
    allow.focus();
  }

  function escapeHtml(text) {
    const div = document.createElement("div");
    div.textContent = text;
    return div.innerHTML;
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
        setOrb("listening", said || "Listening…");
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
    if (current) { try { current.abort(); } catch {} }
  }

  function listenForWake() {
    if (!state.wakeListening || state.busy) return;
    startRecognition("wake");
  }

  function listenForCommand() {
    speech.stop();
    if (startRecognition("command")) {
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
    for (;;) {
      try {
        const response = await fetch("/api/events");
        const data = await response.json();
        for (const event of data.events || []) handle(event);
      } catch {
        await new Promise((resume) => setTimeout(resume, 3000));
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
      el.statusText.textContent = "app not responding";
      return;
    }
    el.name.textContent = data.name || "JARVIS";
    state.wakeWord = data.wake_word || "hey jarvis";
    el.wakeWord.textContent = state.wakeWord;
    el.statusDot.dataset.state = data.ready ? "ready" : "error";
    el.statusText.textContent = data.ready ? data.model : data.detail;
    el.panelModel.textContent = `${data.model} via ${data.backend}`;
    el.panelDetail.textContent = data.detail;
    el.panelTools.innerHTML = "";
    for (const tool of data.tools || []) {
      const li = document.createElement("li");
      li.textContent = tool;
      el.panelTools.append(li);
    }
    el.panelMemory.innerHTML = "";
    for (const item of data.memories || []) {
      const li = document.createElement("li");
      const text = document.createElement("span");
      text.textContent = item.text;
      const remove = document.createElement("button");
      remove.textContent = "×";
      remove.title = "Forget this";
      remove.onclick = async () => {
        await fetch("/api/forget", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ id: item.id }),
        });
        refreshState();
      };
      li.append(text, remove);
      el.panelMemory.append(li);
    }
    if (!data.memories?.length) {
      el.panelMemory.innerHTML = '<li class="muted small">Nothing yet.</li>';
    }
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
      fetch("/api/cancel", { method: "POST" }).catch(() => {});
      setOrb("idle");
      return;
    }
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
    await fetch("/api/reset", { method: "POST" });
    el.transcript.innerHTML = "";
    speech.stop();
    setOrb("idle");
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      speech.stop();
      if (state.busy) fetch("/api/cancel", { method: "POST" }).catch(() => {});
      if (!el.panel.hidden) el.panelToggle.click();
    }
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
  refreshState();
  pollAnnouncements();
  if (state.wakeListening) listenForWake();
  el.input.focus();
})();
