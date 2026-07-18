/**
 * ui.js — Desktop web UI.
 *
 * Layout:
 *   - Top-left HUD: status, live position, view-mode buttons, controls help
 *   - Right sidebar: minimap (top) + tabbed panel (Controls / Chat)
 *
 * The Controls tab is populated by main.js via buildControls(schema) so all
 * bindings (CONFIG ↔ scene) stay in one place. The Chat tab is the future
 * hook for agent.js.
 */
export function createUI({ title = "Spark WebXR Research" } = {}) {
  // ---------- top-left HUD ----------
  const root = document.createElement("div");
  root.id = "hud";
  root.innerHTML = `
    <div class="hud-panel">
      <div class="hud-header">
        <div class="hud-title">${title}</div>
        <button type="button" class="hud-toggle" data-role="toggle"
                title="Collapse panel" aria-label="Collapse panel">&#x2212;</button>
      </div>
      <div class="hud-body" data-role="body">
        <div class="hud-status" data-role="status">Initializing…</div>
        <div class="hud-pos" data-role="pos"></div>
        <div class="hud-view">
          <button type="button" data-mode="first">1st Person</button>
          <button type="button" data-mode="third">3rd Person</button>
        </div>
        <div class="hud-help" data-role="help">
          Click to look around · <b>Esc</b> release mouse<br>
          <b>WASD</b> move · <b>Shift</b> sprint · <b>Q/E</b> down/up (1st person)<br>
          <b>V</b> view · <b>M</b> collider mesh · <b>N</b> scan visual ·
          <b>L</b> raw PLY points · <b>T</b> name tags · <b>B</b> bounding boxes ·
          <b>C</b> auto capture · <b>P</b> spawn point · <b>Enter</b> chat
        </div>
      </div>
    </div>`;
  document.body.appendChild(root);

  const hudPanel = root.querySelector(".hud-panel");
  const bodyEl = root.querySelector('[data-role="body"]');
  const toggleEl = root.querySelector('[data-role="toggle"]');
  const statusEl = root.querySelector('[data-role="status"]');
  const posEl = root.querySelector('[data-role="pos"]');
  const helpEl = root.querySelector('[data-role="help"]');
  const viewButtons = [...root.querySelectorAll(".hud-view button")];

  let hudCollapsed = false;
  function setHudCollapsed(collapsed) {
    hudCollapsed = collapsed;
    hudPanel.classList.toggle("collapsed", collapsed);
    bodyEl.style.display = collapsed ? "none" : "";
    toggleEl.innerHTML = collapsed ? "&#x2b;" : "&#x2212;";
    toggleEl.title = collapsed ? "Expand panel" : "Collapse panel";
    toggleEl.setAttribute("aria-label", toggleEl.title);
  }
  toggleEl.addEventListener("click", (e) => {
    e.stopPropagation();
    setHudCollapsed(!hudCollapsed);
  });

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
        <button type="button" data-tab="controls" class="active">Controls</button>
        <button type="button" data-tab="chat">Chat</button>
        <button type="button" data-tab="objects">Objects</button>
      </div>
      <div class="panel-body" data-tab-panel="controls"></div>
      <div class="panel-body hidden" data-tab-panel="chat">
        <div class="chat-log" data-role="log"></div>
        <form class="chat-row" data-role="form">
          <input type="text" data-role="input"
                 placeholder="Message or command… (Enter)" autocomplete="off" />
          <button type="submit">Send</button>
        </form>
      </div>
      <div class="panel-body hidden" data-tab-panel="objects">
        <div class="obj-list" data-role="obj-list"></div>
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
  const DEFAULT_PLACEHOLDER = inputEl.placeholder;

  let submitHandler = null;
  formEl.addEventListener("submit", (e) => {
    e.preventDefault();
    const text = inputEl.value;
    inputEl.value = "";
    inputEl.blur(); // give WASD control back after sending
    // Empty submits are forwarded too (not swallowed here) — annotations.js
    // tag mode treats an empty submit as "cancel"; onSubmit callers that
    // don't care about that should ignore falsy text themselves.
    submitHandler?.(text.trim());
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

  // ---------- objects tab ----------
  const objListEl = sidebar.querySelector('[data-role="obj-list"]');
  let objectActionHandler = null;

  /** entries: [{ id, label, distance, source }] — source "auto" gets an Accept button. */
  function renderObjects(entries) {
    objListEl.innerHTML = "";
    if (!entries.length) {
      const empty = document.createElement("div");
      empty.className = "obj-empty";
      empty.textContent = "No tagged objects yet. Enable Tag mode (Controls tab) and click the scene.";
      objListEl.appendChild(empty);
      return;
    }
    for (const it of entries) {
      const row = document.createElement("div");
      row.className = "obj-row";

      if (it.source && it.source !== "manual") row.classList.add(`obj-row--${it.source}`);

      const info = document.createElement("div");
      info.className = "obj-info";
      const labelRow = document.createElement("div");
      labelRow.className = "obj-label-row";
      const labelSpan = document.createElement("span");
      labelSpan.className = "obj-label";
      labelSpan.textContent = it.label;
      labelRow.appendChild(labelSpan);
      if (it.source && it.source !== "manual") {
        const badge = document.createElement("span");
        badge.className = `obj-badge obj-badge--${it.source}`;
        badge.textContent = it.source;
        labelRow.appendChild(badge);
      }
      const distSpan = document.createElement("span");
      distSpan.className = "obj-dist";
      distSpan.textContent = `${it.distance.toFixed(1)}m`;
      info.append(labelRow, distSpan);

      const actions = document.createElement("div");
      actions.className = "obj-actions";
      if (it.source === "auto") {
        const acceptBtn = document.createElement("button");
        acceptBtn.type = "button";
        acceptBtn.textContent = "Accept";
        acceptBtn.dataset.action = "accept";
        acceptBtn.dataset.id = it.id;
        actions.append(acceptBtn);
      }
      const renameBtn = document.createElement("button");
      renameBtn.type = "button";
      renameBtn.textContent = "Rename";
      renameBtn.dataset.action = "rename";
      renameBtn.dataset.id = it.id;
      const deleteBtn = document.createElement("button");
      deleteBtn.type = "button";
      deleteBtn.textContent = "Delete";
      deleteBtn.dataset.action = "delete";
      deleteBtn.dataset.id = it.id;
      actions.append(renameBtn, deleteBtn);

      row.append(info, actions);
      objListEl.appendChild(row);
    }
  }
  objListEl.addEventListener("click", (e) => {
    const btn = e.target.closest("button[data-action]");
    if (!btn) return;
    objectActionHandler?.(btn.dataset.action, btn.dataset.id);
  });

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

    /**
     * Fullscreen gallery of captured frames (capture.js). Click a photo
     * for a lightbox; Esc / Close / backdrop click dismisses.
     */
    showCaptureGallery(frames) {
      const overlay = document.createElement("div");
      overlay.className = "cap-overlay";
      overlay.innerHTML = `
        <div class="cap-header">
          <span class="cap-title">Captured frames (${frames.length})</span>
          <button type="button" class="cap-close">Close (Esc)</button>
        </div>
        <div class="cap-grid"></div>`;
      const grid = overlay.querySelector(".cap-grid");

      if (frames.length === 0) {
        grid.innerHTML = `<div class="cap-empty">
          No frames yet — enable Auto capture (C) and move around,
          or press "Capture now".</div>`;
      }
      for (const f of [...frames].reverse()) {
        const cell = document.createElement("div");
        cell.className = "cap-cell";
        const time = new Date(f.timestamp).toLocaleTimeString();
        const p = f.camera.position;
        cell.innerHTML = `
          <img src="${f.image}" alt="frame ${f.id}">
          <div class="cap-meta">#${f.id} · ${time} ·
            cam (${p[0].toFixed(1)}, ${p[1].toFixed(1)}, ${p[2].toFixed(1)})</div>`;
        cell.addEventListener("click", () => {
          const box = document.createElement("div");
          box.className = "cap-lightbox";
          box.innerHTML = `<img src="${f.image}" alt="frame ${f.id}">`;
          box.addEventListener("click", () => box.remove());
          document.body.appendChild(box);
        });
        grid.appendChild(cell);
      }

      function close() {
        window.removeEventListener("keydown", onKey, true);
        overlay.remove();
      }
      function onKey(e) {
        if (e.code === "Escape") {
          const box = document.querySelector(".cap-lightbox");
          if (box) box.remove();
          else close();
          e.stopPropagation();
        }
      }
      window.addEventListener("keydown", onKey, true);
      overlay.querySelector(".cap-close").addEventListener("click", close);
      overlay.addEventListener("click", (e) => {
        if (e.target === overlay) close();
      });
      document.body.appendChild(overlay);
    },

    focusInput() {
      showTab("chat");
      inputEl.focus();
    },
    setInputPlaceholder(text) {
      inputEl.placeholder = text ?? DEFAULT_PLACEHOLDER;
    },
    renderObjects,
    onObjectAction(fn) {
      objectActionHandler = fn;
    },
    dispose() {
      root.remove();
      sidebar.remove();
    },
  };
}
