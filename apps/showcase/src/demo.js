/* Historical checkpoint player. Never invokes an inference or protected endpoint. */
(() => {
  "use strict";
  const $ = id => document.getElementById(id);
  const play = $("demo-play"), previous = $("demo-previous"), next = $("demo-next");
  const restart = $("demo-restart"), speed = $("demo-speed"), progress = $("demo-progress");
  const navigation = $("demo-stages"), workflow = $("demo-workflow");
  const evidence = $("demo-evidence"), sourceBox = $("demo-source");
  let trace, index = 0, timer = null, playing = false;

  function node(tag, className, value) {
    const item = document.createElement(tag);
    if (className) item.className = className;
    if (value !== undefined) item.textContent = String(value);
    return item;
  }
  function stop() {
    if (timer !== null) clearTimeout(timer);
    timer = null;
    playing = false;
    play.textContent = index === trace?.stages.length - 1 ? "Play again" : "Play";
    play.setAttribute("aria-label", play.textContent);
  }
  function tick() {
    if (!playing) return;
    if (index >= trace.stages.length - 1) { stop(); return; }
    timer = setTimeout(() => {
      index += 1;
      render();
      tick();
    }, Number(speed.value));
  }
  function setIndex(value, updateHash = true) {
    if (!trace) return;
    stop();
    index = Math.max(0, Math.min(trace.stages.length - 1, value));
    render();
    if (updateHash) history.replaceState(null, "", "#demo-stage-" + trace.stages[index].id);
  }
  function facts(parent, label, value) {
    const item = node("div", "demo-fact");
    item.append(node("dt", "", label), node("dd", "", value));
    parent.append(item);
  }
  function render() {
    const stage = trace.stages[index];
    navigation.replaceChildren();
    trace.stages.forEach((row, position) => {
      const item = node("li", position < index ? "visited" : position === index ? "current" : "pending");
      const button = node("button", "", `${String(position + 1).padStart(2, "0")}  ${row.title}`);
      button.type = "button";
      button.setAttribute("aria-current", position === index ? "step" : "false");
      button.addEventListener("click", () => setIndex(position));
      item.append(button);
      navigation.append(item);
    });
    workflow.replaceChildren(node("span", "eyebrow", "CHECKPOINT " + (index + 1) + " / " + trace.stages.length),
      node("h3", "", stage.title), node("p", "workflow-summary", stage.summary),
      node("p", "workflow-trust", `Producer / trust: ${stage.trust}`));
    const track = node("div", "workflow-track");
    ["Customer source", "Agent A", "Trusted checks", "Agent B", "Unsent proposal"].forEach((name, position) => {
      track.append(node("span", position === Math.min(4, Math.floor(index / 2)) ? "active" : "", name));
    });
    workflow.append(track);
    evidence.replaceChildren(node("p", "evidence-type", "REPORT-BACKED CHECKPOINT"));
    const dl = node("dl", "demo-facts");
    facts(dl, "Report source", stage.evidence);
    for (const fact of stage.facts) facts(dl, fact.label, fact.value);
    evidence.append(dl);
    progress.textContent = `Stage ${index + 1} of ${trace.stages.length} · ${Math.round((index + 1) / trace.stages.length * 100)}% · ${playing ? "Playing" : "Paused"}`;
    previous.disabled = index === 0;
    next.disabled = index === trace.stages.length - 1;
  }
  function renderSource() {
    const record = trace.source;
    const box = node("blockquote", "source-quote");
    const chars = Array.from(record.text);
    const {start, end} = record.trusted_span;
    box.append(document.createTextNode(chars.slice(0, start).join("")),
      node("mark", "", chars.slice(start, end).join("")),
      document.createTextNode(chars.slice(end).join("")));
    const dl = node("dl", "demo-facts");
    facts(dl, "Selected by model", record.model_selected_quote);
    facts(dl, "Derived by trusted code", `[${start},${end}) ${record.trusted_span.unit}`);
    facts(dl, "Immutable source", `${record.source_id} · v${record.version} · ${record.ticket_id}`);
    facts(dl, "SHA-256", record.sha256);
    sourceBox.replaceChildren(box, dl, node("p", "source-limit", "Exact occurrence and relevance are necessary checks, not proof of manager authority or present customer intent."));
  }
  play.addEventListener("click", () => {
    if (!trace) return;
    if (playing) { stop(); render(); return; }
    if (index === trace.stages.length - 1) index = 0;
    playing = true;
    play.textContent = "Pause";
    play.setAttribute("aria-label", "Pause playback");
    render();
    tick();
  });
  previous.addEventListener("click", () => setIndex(index - 1));
  next.addEventListener("click", () => setIndex(index + 1));
  restart.addEventListener("click", () => setIndex(0));
  speed.addEventListener("change", () => { if (playing) { clearTimeout(timer); tick(); } });
  window.addEventListener("hashchange", () => {
    const id = location.hash.slice("#demo-stage-".length);
    if (trace && location.hash.startsWith("#demo-stage-")) {
      const found = trace.stages.findIndex(stage => stage.id === id);
      if (found >= 0) setIndex(found, false);
    }
  });
  fetch("./trace.json", {cache: "no-store"}).then(response => {
    if (!response.ok) throw new Error("historical trace unavailable");
    return response.json();
  }).then(payload => {
    if (payload.schema !== "scoperelay-recorded-checkpoints-v1" || payload.classification !== "RECORDED_REPLAY" ||
        payload.playback_is_inference !== false || payload.new_generations !== 0 ||
        payload.scenario_id !== "SC-01" || payload.protected_resource !== "NOT_ATTEMPTED" ||
        !Array.isArray(payload.stages) || payload.stages.length !== 10 ||
        Array.from(payload.source.text).slice(payload.source.trusted_span.start, payload.source.trusted_span.end).join("") !== payload.source.model_selected_quote) {
      throw new Error("unsupported historical trace manifest");
    }
    trace = payload;
    const deepLink = location.hash.startsWith("#demo-stage-") ? location.hash.slice("#demo-stage-".length) : "";
    index = Math.max(0, trace.stages.findIndex(stage => stage.id === deepLink));
    render();
    renderSource();
  }).catch(() => {
    progress.textContent = "Verified record unavailable; playback disabled.";
    [play, previous, next, restart, speed].forEach(control => control.disabled = true);
    workflow.replaceChildren(node("p", "load-error", "The verified historical manifest could not be loaded. No replay is shown."));
  });
})();
