/**
 * ui.js — Desktop web UI.
 *
 * Layout:
 *   - Top-left HUD: status, live position, view-mode buttons, controls help
 *   - Right sidebar: minimap (top) + tabbed panel (컨트롤 / 채팅)
 *
 * The 컨트롤 tab is populated by main.js via buildControls(schema) so all
 * bindings (CONFIG ↔ scene) stay in one place. The 채팅 tab is the future
 * hook for agent.js.
 */
export function createUI({ title = "Spark WebXR Research" } = {}) {
  // ---------- top-left HUD ----------
  const root = document.createElement("div");
  root.id = "hud";
  root.innerHTML = `
    <div class="hud-panel">
      <div class="hud-title">${title}</div>
      <div class="hud-status" data-role="status">Initializing…</div>
      <div class="hud-pos" data-role="pos"></div>
      <div class="hud-view">
        <button type="button" data-mode="first">1인칭</button>
        <button type="button" data-mode="third">3인칭</button>
      </div>
      <div class="hud-help" data-role="help">
        Click to look around · <b>Esc</b> release mouse<br>
        <b>WASD</b> move · <b>Shift</b> sprint · <b>Q/E</b> down/up (1인칭)<br>
        <b>V</b> view · <b>M</b> collider gizmo · <b>N</b> splat ·
        <b>P</b> spawn point · <b>Enter</b> chat
      </div>
    </div>`;
  document.body.appendChild(root);

  const statusEl = root.querySelector('[data-role="status"]');
  const posEl = root.querySelector('[data-role="pos"]');
  const helpEl = root.querySelector('[data-role="help"]');
  const viewButtons = [...root.querySelectorAll(".hud-view button")];

  let viewChangeHandler = null;
  for (const btn of viewButtons) {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      viewChangeHandler?.(btn.dataset.mode);
    });
  }

  function setViewMode(mode) {
    for (const btn of viewButtons) {
      btn.classList.toggle("active", btn.dataset.mode === mode);
    }
  }

  // ---------- right sidebar ----------
  const sidebar = document.createElement("div");
  sidebar.id = "sidebar";
  sidebar.innerHTML = `
    <div data-role="minimap-mount"></div>
    <div class="panel">
      <div class="panel-tabs">
        <button type="button" data-tab="controls" class="active">컨트롤</button>
        <button type="button" data-tab="chat">채팅</button>
      </div>
      <div class="panel-body" data-tab-panel="controls"></div>
      <div class="panel-body hidden" data-tab-panel="chat">
        <div class="chat-log" data-role="log"></div>
        <form class="chat-row" data-role="form">
          <input type="text" data-role="input"
                 placeholder="메시지나 명령… (Enter)" autocomplete="off" />
          <button type="submit">전송</button>
        </form>
      </div>
    </div>`;
  document.body.appendChild(sidebar);

  const minimapMount = sidebar.querySelector('[data-role="minimap-mount"]');
  const tabButtons = [...sidebar.querySelectorAll(".panel-tabs button")];
  const tabPanels = [...sidebar.querySelectorAll("[data-tab-panel]")];

  function showTab(name) {
    for (const b of tabButtons) b.classList.toggle("active", b.dataset.tab === name);
    for (const p of tabPanels) p.classList.toggle("hidden", p.dataset.tabPanel !== name);
  }
  for (const b of tabButtons) {
    b.addEventListener("click", () => showTab(b.dataset.tab));
  }

  // ---------- controls builder ----------
  const controlsPanel = sidebar.querySelector('[data-tab-panel="controls"]');
  const controlRefs = new Map(); // id → { input, valueEl } | { input }

  /**
   * schema: array of
   *  { type: "section", label }
   *  { type: "slider", id, label, min, max, step, value, onChange, format? }
   *  { type: "checkbox", id, label, value, onChange }
   *  { type: "button", label, onClick }
   */
  function buildControls(schema) {
    controlsPanel.innerHTML = "";
    controlRefs.clear();
    for (const item of schema) {
      if (item.type === "section") {
        const h = document.createElement("div");
        h.className = "ctl-section";
        h.textContent = item.label;
        controlsPanel.appendChild(h);
      } else if (item.type === "slider") {
        const row = document.createElement("label");
        row.className = "ctl-row";
        const fmt = item.format ?? ((v) => (+v).toFixed(2));
        row.innerHTML = `
          <span class="ctl-label">${item.label}</span>
          <input type="range" min="${item.min}" max="${item.max}"
                 step="${item.step}" value="${item.value}">
          <span class="ctl-value">${fmt(item.value)}</span>`;
        const input = row.querySelector("input");
        const valueEl = row.querySelector(".ctl-value");
        input.addEventListener("input", () => {
          valueEl.textContent = fmt(input.value);
          item.onChange?.(parseFloat(input.value));
        });
        controlsPanel.appendChild(row);
        controlRefs.set(item.id, { input, valueEl, fmt });
      } else if (item.type === "checkbox") {
        const row = document.createElement("label");
        row.className = "ctl-row ctl-check";
        row.innerHTML = `
          <input type="checkbox" ${item.value ? "checked" : ""}>
          <span class="ctl-label">${item.label}</span>`;
        const input = row.querySelector("input");
        input.addEventListener("change", () => item.onChange?.(input.checked));
        controlsPanel.appendChild(row);
        controlRefs.set(item.id, { input });
      } else if (item.type === "button") {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "ctl-button";
        btn.textContent = item.label;
        btn.addEventListener("click", () => item.onClick?.());
        controlsPanel.appendChild(btn);
      }
    }
  }

  /** Sync a control's displayed value from outside (e.g. hotkeys). */
  function setControl(id, value) {
    const ref = controlRefs.get(id);
    if (!ref) return;
    if (ref.input.type === "checkbox") {
      ref.input.checked = !!value;
    } else {
      ref.input.value = value;
      if (ref.valueEl) ref.valueEl.textContent = ref.fmt(value);
    }
  }

  // ---------- chat ----------
  const logEl = sidebar.querySelector('[data-role="log"]');
  const formEl = sidebar.querySelector('[data-role="form"]');
  const inputEl = sidebar.querySelector('[data-role="input"]');

  let submitHandler = null;
  formEl.addEventListener("submit", (e) => {
    e.preventDefault();
    const text = inputEl.value.trim();
    if (!text) return;
    inputEl.value = "";
    inputEl.blur(); // give WASD control back after sending
    submitHandler?.(text);
  });

  function addMessage(from, text) {
    const line = document.createElement("div");
    line.className = "chat-line";
    line.innerHTML = `<b>${from}</b> ${text}`;
    logEl.appendChild(line);
    while (logEl.children.length > 50) logEl.firstChild.remove();
    logEl.scrollTop = logEl.scrollHeight;
    showTab("chat");
  }

  return {
    setStatus(text) {
      statusEl.textContent = text;
    },
    setPosition(x, y, z) {
      posEl.textContent = `pos [${x.toFixed(1)}, ${y.toFixed(1)}, ${z.toFixed(1)}]`;
    },
    setHelpVisible(visible) {
      helpEl.style.display = visible ? "" : "none";
    },
    setViewMode,
    onViewChange(fn) {
      viewChangeHandler = fn;
    },
    minimapMount,
    buildControls,
    setControl,
    onSubmit(fn) {
      submitHandler = fn;
    },
    addMessage,
    focusInput() {
      showTab("chat");
      inputEl.focus();
    },
    dispose() {
      root.remove();
      sidebar.remove();
    },
  };
}
