(() => {
  "use strict";
  const $ = id => document.getElementById(id);
  const message = $("form-message"), button = $("submit"), timeline = $("timeline");
  let mode = "GUIDED", csrf = "", liveAvailable = false;
  const liveTab = document.createElement("button");
  liveTab.type = "button";
  liveTab.className = "tab";
  liveTab.dataset.mode = "LIVE";
  liveTab.setAttribute("aria-pressed", "false");
  liveTab.textContent = "Local live SC-01";
  document.querySelector(".tabs").append(liveTab);
  const inviteField = document.createElement("div");
  inviteField.hidden = true;
  const inviteLabel = document.createElement("label");
  inviteLabel.htmlFor = "beta-invite";
  inviteLabel.textContent = "Restricted beta invite";
  const inviteInput = document.createElement("input");
  inviteInput.id = "beta-invite";
  inviteInput.type = "password";
  inviteInput.autocomplete = "off";
  inviteInput.maxLength = 128;
  inviteField.append(inviteLabel, inviteInput);
  $("run-form").insertBefore(inviteField, button);
  function text(tag, value, className = "") {
    const item = document.createElement(tag);
    item.textContent = value == null ? "" : String(value);
    if (className) item.className = className;
    return item;
  }
  function setMessage(value) { message.textContent = value; }
  const explanations = {
    TICKET_SIZE_INVALID: "Use 12–600 characters of synthetic ticket text.",
    REAL_DATA_OR_URL_NOT_ALLOWED: "Remove links or contact details. Use synthetic examples only.",
    OFFLINE_CAPACITY_EXHAUSTED: "Today's offline capacity is exhausted. No new run was started.",
    SESSION_CAPACITY_EXHAUSTED: "Session capacity is reached. Please try later.",
    INVITE_REQUIRED: "Enter the restricted beta invite to start a session.",
    CLIENT_SESSION_RATE_LIMIT: "Too many sessions from this client. Try again in a minute.",
    CLIENT_RUN_RATE_LIMIT: "Too many runs from this client. Try again in a minute.",
    SESSION_RUN_LIMIT: "This session has reached its run limit.",
    CUSTOM_RUN_LIMIT: "This session has reached its custom-ticket limit.",
    CHALLENGE_RUN_LIMIT: "This session has reached its challenge limit.",
    SANDBOX_STORAGE_UNAVAILABLE: "Sandbox state is unavailable. No new run was started.",
    SESSION_EXPIRED_OR_INVALID: "Session expired. Start a new synthetic run; old evidence is unavailable.",
    WORKER_INTERRUPTED: "The prior worker stopped before completion. Its reserved capacity was not refunded.",
    PROVIDER_TIMEOUT_SIMULATED: "The offline provider double timed out; no protected action occurred.",
    PROVIDER_RATE_LIMIT_SIMULATED: "The offline provider double returned a rate-limit failure.",
    AGENT_VALIDATION_FAILED: "A model-shaped response or source citation failed trusted validation."
    ,LIVE_UNAVAILABLE: "Local inference has not been separately authorized and enabled. Offline cases still work."
    ,LIVE_SYNTHETIC_SC01_ONLY: "Live mode accepts only the fixed synthetic SC-01 ticket."
  };
  async function json(url, options = {}) {
    const response = await fetch(url, {cache: "no-store", credentials: "same-origin", ...options});
    let result;
    try { result = await response.json(); } catch { throw new Error("The local sandbox response was unavailable."); }
    if (!response.ok) throw new Error(result.code || "Sandbox request failed");
    return result;
  }
  async function session() {
    if (csrf) return;
    try { csrf = (await json("/api/sandbox/session")).csrf; }
    catch (error) {
      if (!String(error.message).includes("SESSION_")) throw error;
      const headers = inviteField.hidden ? {} : {"X-ScopeRelay-Invite": inviteInput.value};
      csrf = (await json("/api/sandbox/session", {method: "POST", headers})).csrf;
      inviteInput.value = "";
    }
  }
  function render(result) {
    const kind = result.mode === "LIVE" ? "LOCAL LIVE INFERENCE" : "OFFLINE / DETERMINISTIC";
    const count = result.request_count_note === "UNKNOWN_AFTER_INTERRUPTION" ?
      "generation count uncertain after interruption" : `model requests: ${result.model_requests || 0}`;
    $("result-summary").textContent = `${result.scenario_id} · ${result.status} · run ${result.run_id} · ${result.started_at} · decision: ${result.decision || "not reached"} · B: ${result.b_status || "not reached"} · ${kind} · ${count} · protected effect: disabled`;
    timeline.replaceChildren();
    for (const event of result.timeline || []) {
      const item = text("li", "", event.status === "FAILED" || event.status === "REJECTED" ? "failure" : "");
      item.append(text("strong", event.stage.replaceAll("_", " ")));
      const details = [event.agent, event.tool, event.status, event.source_owner,
        event.provider && result.mode === "LIVE" ? `${event.provider} / ${event.requested_model}` : null,
        event.observation_sent ? "observation delivered" : null,
        event.continued ? "same-conversation continuation" : null,
        event.relevance, event.diagnostic, event.purpose,
        event.derived_span ? `trusted span [${event.derived_span.start}, ${event.derived_span.end})` : null,
        event.scripted_quote ? `scripted quotation: ${event.scripted_quote}` : null,
        event.model_quote ? `model quotation: ${event.model_quote}` : null]
        .filter(Boolean).join(" · ");
      item.append(text("small", details || "Recorded without further detail"));
      timeline.append(item);
    }
    if (result.failure_code) timeline.append(text("li", `Failure: ${result.failure_code}${explanations[result.failure_code] ? " — " + explanations[result.failure_code] : ""}`, "failure"));
    const source = $("source-panel");
    source.hidden = !result.source;
    if (result.source) {
      $("source-text").textContent = result.source.text;
      const dl = $("citation"); dl.replaceChildren();
      for (const [label, value] of [["Source version", result.source.version], ["Source SHA-256", result.source.sha256]]) {
        dl.append(text("dt", label), text("dd", value));
      }
    }
  }
  async function showRun(runId) {
    const result = await json(`/api/sandbox/runs/${encodeURIComponent(runId)}/evidence`);
    render(result);
  }
  document.querySelectorAll("[data-mode]").forEach(tab => tab.addEventListener("click", () => {
    mode = tab.dataset.mode;
    document.querySelectorAll("[data-mode]").forEach(item => {
      item.classList.toggle("selected", item === tab);
      item.setAttribute("aria-pressed", String(item === tab));
    });
    $("guided-fields").hidden = mode !== "GUIDED";
    $("challenge-fields").hidden = mode !== "CHALLENGE";
    $("custom-fields").hidden = mode !== "CUSTOM";
    $("submit").textContent = mode === "LIVE" ? "Run local live SC-01" : "Run offline scenario";
    setMessage(mode === "LIVE" ? (liveAvailable ?
      "Local Ollama is operator-enabled. This will request up to four new model generations." :
      "LIVE UNAVAILABLE: local inference requires separate operator authorization. Offline modes remain available.") :
      "Offline mode uses deterministic test doubles; it does not invoke a model.");
  }));
  $("run-form").addEventListener("submit", async event => {
    event.preventDefault();
    if (mode === "LIVE" && !liveAvailable) { setMessage(explanations.LIVE_UNAVAILABLE); return; }
    button.disabled = true;
    setMessage(mode === "LIVE" ? "Running new local A → B inference…" :
      "Running the scoped offline A → B workflow…");
    try {
      await session();
      const body = {mode: mode === "CHALLENGE" || mode === "LIVE" ? "GUIDED" : mode,
        scenario_id: mode === "CHALLENGE" ? $("challenge").value : "SC-01",
        ticket_text: mode === "CUSTOM" ? $("ticket").value : null,
        run_kind: mode === "LIVE" ? "LIVE" : "DETERMINISTIC_COMPONENT_TEST",
        idempotency_key: crypto.randomUUID()};
      const started = await json("/api/sandbox/runs", {method: "POST",
        headers: {"Content-Type": "application/json", "X-ScopeRelay-CSRF": csrf},
        body: JSON.stringify(body)});
      sessionStorage.setItem("sr_last_run", started.run_id);
      await showRun(started.run_id);
      setMessage(mode === "LIVE" ? "Local run finished. Inspect actual requests and validation above; no protected action was attempted." :
        "Complete. This is scripted offline evidence, not model inference.");
    } catch (error) {
      const code = error.message || "Sandbox request failed";
      setMessage(explanations[code] ? `${code} — ${explanations[code]}` : code);
      if (String(error.message).includes("SESSION_")) {
        csrf = "";
      }
    } finally { button.disabled = false; }
  });
  json("/api/sandbox/capacity").then(async status => {
    liveAvailable = status.live === "LOCAL_OLLAMA_ONLY";
    if (liveAvailable) {
      document.querySelector(".top-status").textContent = "LOCAL · LIVE OPTION AVAILABLE";
      document.querySelector(".hero .lead").textContent = "Run synthetic support tickets through the scoped A and B read-tool pipeline. Offline tests use deterministic turns; an operator-authorized local Ollama run makes new model requests only when you select Local live SC-01.";
      document.querySelector(".hero-pills span").textContent = "Local inference option enabled";
      document.querySelector(".hero-card strong").textContent = "Offline or local live";
      document.querySelector(".hero-card p").textContent = "Agent A reads a versioned ticket; trusted code binds the exact quotation and verifies scope. Agent B reads independently verified order status. Local live runs use new Ollama model requests only after you select the option.";
      document.querySelector(".limits p:last-child").textContent = "Offline cases are DETERMINISTIC_COMPONENT_TEST; local live SC-01 is labeled LIVE only after real model requests. A quote proves source presence, not customer intent. No API-108/API-109, queue effect or external communication is available. Protected-action release remains blocked.";
    }
    inviteField.hidden = !status.invite_required;
    if (status.sandbox === "DISABLED") { button.disabled = true; setMessage("Local sandbox gate is disabled. Hosted LIVE is also disabled."); return; }
    if (!status.accepting_new_runs) { button.disabled = true; setMessage("The local sandbox kill switch is active. New runs are disabled."); return; }
    setMessage(`Local capacity: ${status.offline_turn_slots_remaining} reserved turn slots. ${liveAvailable ? "Local Ollama option enabled; no model call occurs until you choose it." : "LIVE unavailable."}`);
    const last = sessionStorage.getItem("sr_last_run");
    if (last) { try { await session(); await showRun(last); } catch { sessionStorage.removeItem("sr_last_run"); } }
  }).catch(() => { button.disabled = true; setMessage("Local sandbox API is unavailable."); });
})();
