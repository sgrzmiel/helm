"use strict";

const state = {
  tickets: [],
  proposal: { updates: [], creates: [], closes: [], links: [], notes: null },
  // Untouched snapshot of what Claude proposed - sent alongside `proposal` on
  // apply so the server can log user edits and refine the prompt over time.
  originalProposal: null,
  contextExcerpt: null,
  // closes flagged "excluded" still render but are not sent on apply.
  excludedCloses: new Set(),
  // When promoting an Idea, we stash its metadata here so we can attach it to
  // the resulting Epic once apply() reports the new Jira key.
  fromIdea: null,
};

const $ = (id) => document.getElementById(id);

const escapeHtml = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));

function statusBadge(snapshot) {
  const cat = snapshot.status_category;
  if (cat === "new") return `<span class="badge badge-new">${escapeHtml(snapshot.status)}</span>`;
  if (cat === "indeterminate")
    return `<span class="badge badge-progress">${escapeHtml(snapshot.status)}</span><span class="badge badge-locked">LOCKED</span>`;
  if (cat === "done")
    return `<span class="badge badge-done">${escapeHtml(snapshot.status)}</span><span class="badge badge-locked">LOCKED</span>`;
  return `<span class="badge">${escapeHtml(snapshot.status)}</span>`;
}

function findSnapshot(key) {
  return state.tickets.find((t) => t.key === key);
}

// ---------- Render ----------

function renderSnapshots() {
  const section = $("snapshots-section");
  const container = $("snapshots");
  if (!state.tickets.length) {
    section.classList.add("hidden");
    return;
  }
  section.classList.remove("hidden");
  container.innerHTML = state.tickets
    .map((t) => `
      <div class="snapshot">
        <div class="key">${escapeHtml(t.key)}${jiraLinkIcon(t.key, t.summary)}</div>
        <div class="summary">${escapeHtml(t.summary)}</div>
        <div>${statusBadge(t)}</div>
        <div class="status">${escapeHtml(t.assignee || "-")}</div>
      </div>
    `)
    .join("");
}

function renderUpdates() {
  const container = $("updates");
  $("updates-count").textContent = `(${state.proposal.updates.length})`;
  container.innerHTML = state.proposal.updates
    .map((u, idx) => {
      const snap = findSnapshot(u.key) || {};
      const f = u.fields || {};
      const fieldRow = (name, current, proposedVal, multiline) => {
        if (proposedVal === null || proposedVal === undefined) return "";
        const currentHtml = current
          ? `<div class="field-current">${escapeHtml(typeof current === "object" ? JSON.stringify(current) : current)}</div>`
          : `<div class="field-current">(unset)</div>`;
        const input = multiline
          ? `<textarea rows="${Math.max(4, String(proposedVal).split("\n").length + 1)}" data-update="${idx}" data-field="${name}">${escapeHtml(proposedVal)}</textarea>`
          : `<input type="text" value="${escapeHtml(typeof proposedVal === "object" ? JSON.stringify(proposedVal) : proposedVal)}" data-update="${idx}" data-field="${name}" />`;
        return `
          <div class="field">
            <div class="field-label">${name} (current → proposed)</div>
            ${currentHtml}
            ${input}
          </div>
        `;
      };
      return `
        <div class="card">
          <div class="header">
            <div><span class="key">${escapeHtml(u.key)}</span>${jiraLinkIcon(u.key, snap.summary)} <span style="color:#6b778c">${escapeHtml(snap.summary || "")}</span></div>
            <div class="badges">${snap.key ? statusBadge(snap) : ""}<button class="secondary" data-action="remove-update" data-idx="${idx}">remove</button></div>
          </div>
          ${fieldRow("summary", snap.summary, f.summary, false)}
          ${fieldRow("description", snap.description, f.description, true)}
          ${fieldRow("duedate", snap.duedate, f.duedate, false)}
          ${fieldRow("labels", (snap.labels || []).join(", "), f.labels ? f.labels.join(", ") : null, false)}
          ${fieldRow("priority", snap.priority, f.priority, false)}
          <div class="reasoning">${escapeHtml(u.reasoning)}</div>
        </div>
      `;
    })
    .join("");
}

function renderCreates() {
  const container = $("creates");
  $("creates-count").textContent = `(${state.proposal.creates.length})`;
  container.innerHTML = state.proposal.creates
    .map((c, idx) => `
      <div class="card">
        <div class="header">
          <div><span class="key">${escapeHtml(c.temp_id)}</span> <span style="color:#6b778c">[${escapeHtml(c.project)} · ${escapeHtml(c.issuetype)}]</span></div>
          <div class="badges"><button class="secondary" data-action="remove-create" data-idx="${idx}">remove</button></div>
        </div>
        <div class="field">
          <div class="field-label">project</div>
          <select data-create="${idx}" data-field="project">
            <option value="KAHOOT" ${c.project === "KAHOOT" ? "selected" : ""}>KAHOOT (frontend)</option>
            <option value="BACK" ${c.project === "BACK" ? "selected" : ""}>BACK (backend)</option>
          </select>
        </div>
        <div class="field">
          <div class="field-label">summary</div>
          <input type="text" value="${escapeHtml(c.summary)}" data-create="${idx}" data-field="summary" />
        </div>
        <div class="field">
          <div class="field-label">description (markdown)</div>
          <textarea rows="${Math.max(6, c.description.split("\n").length + 1)}" data-create="${idx}" data-field="description">${escapeHtml(c.description)}</textarea>
        </div>
        <div class="field">
          <div class="field-label">issuetype</div>
          <input type="text" value="${escapeHtml(c.issuetype)}" data-create="${idx}" data-field="issuetype" />
        </div>
        <div class="field">
          <div class="field-label">labels (comma-separated)</div>
          <input type="text" value="${escapeHtml((c.labels || []).join(", "))}" data-create="${idx}" data-field="labels" />
        </div>
        <div class="field">
          <div class="field-label">components (comma-separated)${c.project === "BACK" ? " - <strong style='color:#bf2600'>required for BACK</strong>" : ""}</div>
          <input type="text" value="${escapeHtml((c.components || []).join(", "))}" data-create="${idx}" data-field="components" placeholder="${c.project === "BACK" ? "BACK project rejects creates without at least one component" : "optional"}" />
        </div>
        <div class="field">
          <div class="field-label">priority</div>
          <input type="text" value="${escapeHtml(c.priority || "")}" data-create="${idx}" data-field="priority" />
        </div>
        <div class="field">
          <div class="field-label">duedate (YYYY-MM-DD)</div>
          <input type="text" value="${escapeHtml(c.duedate || "")}" data-create="${idx}" data-field="duedate" />
        </div>
        <div class="field">
          <div class="field-label">parent_key (epic)</div>
          <input type="text" value="${escapeHtml(c.parent_key || "")}" data-create="${idx}" data-field="parent_key" />
        </div>
        <div class="reasoning">${escapeHtml(c.reasoning)}</div>
      </div>
    `)
    .join("");
}

function renderCloses() {
  const container = $("closes");
  $("closes-count").textContent = `(${state.proposal.closes.length})`;
  container.innerHTML = state.proposal.closes
    .map((c, idx) => {
      const snap = findSnapshot(c.key) || {};
      const excluded = state.excludedCloses.has(idx);
      return `
        <div class="card ${excluded ? "excluded" : ""}">
          <div class="header">
            <div><span class="key">${escapeHtml(c.key)}</span>${jiraLinkIcon(c.key, snap.summary)} <span style="color:#6b778c">${escapeHtml(snap.summary || "")}</span></div>
            <div class="badges">${snap.key ? statusBadge(snap) : ""}<button class="secondary" data-action="toggle-close" data-idx="${idx}">${excluded ? "include" : "exclude"}</button></div>
          </div>
          <div class="field">
            <div class="field-label">transition (optional - leave blank to auto-pick Won't Do / Cancelled / Done)</div>
            <input type="text" value="${escapeHtml(c.transition_name || "")}" data-close="${idx}" data-field="transition_name" />
          </div>
          <div class="reasoning">${escapeHtml(c.reasoning)}</div>
        </div>
      `;
    })
    .join("");
}

function renderLinks() {
  const container = $("links");
  $("links-count").textContent = `(${state.proposal.links.length})`;
  container.innerHTML = state.proposal.links
    .map((l, idx) => `
      <div class="card">
        <div class="header">
          <div>
            <input type="text" value="${escapeHtml(l.from_ref)}" data-link="${idx}" data-field="from_ref" style="width:140px" />
            <select data-link="${idx}" data-field="type" style="width:100px">
              <option value="Blocks" ${l.type === "Blocks" ? "selected" : ""}>Blocks</option>
              <option value="Relates" ${l.type === "Relates" ? "selected" : ""}>Relates</option>
            </select>
            <input type="text" value="${escapeHtml(l.to_ref)}" data-link="${idx}" data-field="to_ref" style="width:140px" />
          </div>
          <div class="badges"><button class="secondary" data-action="remove-link" data-idx="${idx}">remove</button></div>
        </div>
        <div class="reasoning">${escapeHtml(l.reasoning)}</div>
      </div>
    `)
    .join("");
}

function renderAll() {
  renderSnapshots();
  $("proposal-section").classList.remove("hidden");
  $("proposal-notes").textContent = state.proposal.notes || "";
  renderUpdates();
  renderCreates();
  renderCloses();
  renderLinks();
}

// ---------- Edit handlers ----------

document.addEventListener("input", (e) => {
  const t = e.target;
  if (t.dataset.update !== undefined) {
    const idx = +t.dataset.update;
    const field = t.dataset.field;
    let value = t.value;
    if (field === "labels") value = value.split(",").map((s) => s.trim()).filter(Boolean);
    state.proposal.updates[idx].fields[field] = value === "" ? null : value;
  } else if (t.dataset.create !== undefined) {
    const idx = +t.dataset.create;
    const field = t.dataset.field;
    let value = t.value;
    if (field === "labels" || field === "components") {
      value = value.split(",").map((s) => s.trim()).filter(Boolean);
      state.proposal.creates[idx][field] = value;
    } else {
      state.proposal.creates[idx][field] = value === "" ? null : value;
    }
  } else if (t.dataset.close !== undefined) {
    const idx = +t.dataset.close;
    const field = t.dataset.field;
    state.proposal.closes[idx][field] = t.value === "" ? null : t.value;
  } else if (t.dataset.link !== undefined) {
    const idx = +t.dataset.link;
    const field = t.dataset.field;
    state.proposal.links[idx][field] = t.value;
  }
});

document.addEventListener("change", (e) => {
  const t = e.target;
  if (t.tagName === "SELECT") {
    if (t.dataset.create !== undefined) state.proposal.creates[+t.dataset.create][t.dataset.field] = t.value;
    if (t.dataset.link !== undefined) state.proposal.links[+t.dataset.link][t.dataset.field] = t.value;
  }
});

document.addEventListener("click", (e) => {
  const action = e.target.dataset.action;
  if (!action) return;
  const idx = +e.target.dataset.idx;
  if (action === "remove-update") state.proposal.updates.splice(idx, 1);
  if (action === "remove-create") {
    const removedTempId = state.proposal.creates[idx]?.temp_id;
    state.proposal.creates.splice(idx, 1);
    // Drop any links referencing this temp_id - otherwise apply would 404
    if (removedTempId) {
      const before = state.proposal.links.length;
      state.proposal.links = state.proposal.links.filter(
        (l) => l.from_ref !== removedTempId && l.to_ref !== removedTempId,
      );
      const dropped = before - state.proposal.links.length;
      if (dropped > 0) {
        $("generate-status").textContent = `removed ${removedTempId} + ${dropped} link(s) referencing it`;
        $("generate-status").className = "status";
      }
    }
  }
  if (action === "remove-link") state.proposal.links.splice(idx, 1);
  if (action === "toggle-close") {
    if (state.excludedCloses.has(idx)) state.excludedCloses.delete(idx);
    else state.excludedCloses.add(idx);
  }
  renderAll();
});

// ---------- API ----------

// Status-only spinner: same elapsed-time idea but doesn't touch a button.
// Use for dynamically-rendered triggers where we don't have a stable button id.
function startStatusSpinner(statusId, baseLabel) {
  const status = $(statusId);
  if (!status) return (text, isError) => {};
  status.className = "status loading";
  const started = Date.now();
  const renderStatus = () => {
    const sec = Math.floor((Date.now() - started) / 1000);
    status.innerHTML = `<span class="spinner"></span> ${escapeHtml(baseLabel)} <span class="elapsed">(${sec}s)</span>`;
  };
  renderStatus();
  const timer = setInterval(renderStatus, 1000);
  return function stop(finalText, isError = false) {
    clearInterval(timer);
    status.className = isError ? "status error" : "status";
    status.textContent = finalText;
  };
}

// Visible "still working" indicator: spinner + elapsed-time ticker. Returns a
// stop() function that resets the UI to its idle state. Use it around any slow
// async op (plan, apply, extract).
function startLoading(btnId, statusId, baseLabel, busyLabel) {
  const btn = $(btnId);
  const status = $(statusId);
  const originalText = btn.textContent;
  btn.disabled = true;
  btn.textContent = busyLabel;
  btn.classList.add("is-loading");
  status.className = "status loading";
  const started = Date.now();
  const renderStatus = () => {
    const sec = Math.floor((Date.now() - started) / 1000);
    status.innerHTML = `<span class="spinner"></span> ${escapeHtml(baseLabel)} <span class="elapsed">(${sec}s)</span>`;
  };
  renderStatus();
  const timer = setInterval(renderStatus, 1000);
  return function stop(finalText, isError = false) {
    clearInterval(timer);
    btn.disabled = false;
    btn.textContent = originalText;
    btn.classList.remove("is-loading");
    status.className = isError ? "status error" : "status";
    status.textContent = finalText;
  };
}

async function generatePlan() {
  const ticketsSource = $("tickets-source").value;
  const context = $("context").value;
  const figmaUrl = $("figma-url").value;
  if (!context.trim()) {
    $("generate-status").textContent = "context is required";
    $("generate-status").className = "status error";
    return;
  }
  const mode = ticketsSource.trim() ? "fetching tickets" : "epic-creation mode";
  const figma = figmaUrl.trim() ? " + Figma" : "";
  const stop = startLoading(
    "generate-btn",
    "generate-status",
    `${mode}${figma} and asking Claude…`,
    "Generating…",
  );

  try {
    const resp = await fetch("/api/plan", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        tickets_source: ticketsSource || null,
        context,
        figma_url: figmaUrl || null,
      }),
    });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({ detail: resp.statusText }));
      throw new Error(err.detail || resp.statusText);
    }
    const data = await resp.json();
    state.tickets = data.tickets;
    state.proposal = data.proposal;
    // Deep-clone the proposal so subsequent edits don't mutate the snapshot
    state.originalProposal = JSON.parse(JSON.stringify(data.proposal));
    state.contextExcerpt = (context || "").slice(0, 300);
    state.excludedCloses = new Set();
    renderAll();
    stop(`plan ready: ${state.proposal.updates.length} updates, ${state.proposal.creates.length} creates, ${state.proposal.closes.length} closes, ${state.proposal.links.length} links`);
  } catch (e) {
    stop(`error: ${e.message}`, true);
  }
}

async function applyChanges() {
  const proposal = {
    updates: state.proposal.updates,
    creates: state.proposal.creates,
    closes: state.proposal.closes.filter((_, idx) => !state.excludedCloses.has(idx)),
    links: state.proposal.links,
    notes: state.proposal.notes,
  };

  const stop = startLoading("apply-btn", "apply-status", "applying changes to Jira…", "Applying…");

  try {
    const resp = await fetch("/api/apply", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        proposal,
        original_proposal: state.originalProposal,
        context_excerpt: state.contextExcerpt,
      }),
    });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({ detail: resp.statusText }));
      throw new Error(err.detail || resp.statusText);
    }
    const data = await resp.json();
    $("results-section").classList.remove("hidden");
    $("results").innerHTML = data.outcomes
      .map((o) => `<div class="result-item ${o.ok ? "ok" : "err"}">${o.ok ? "✓" : "✗"} ${escapeHtml(o.kind)} ${ticketKey(o.ref)} - ${escapeHtml(o.detail)}</div>`)
      .join("");
    const failed = data.outcomes.filter((o) => !o.ok).length;
    stop(failed ? `done with ${failed} failure(s) - see results below` : `done: ${data.outcomes.length} change(s) applied`);
    // If this apply was promoting an Idea, attach its metadata to the new Epic
    // (and patch the Idea with the resulting Jira key). Best-effort; never
    // blocks the success path.
    await linkIdeaToCreatedEpic(data.outcomes);
  } catch (e) {
    stop(`error: ${e.message}`, true);
  }
}

async function linkIdeaToCreatedEpic(outcomes) {
  if (!state.fromIdea) return;
  // The Epic create outcome's `detail` looks like:
  //   "created FE-123 + tracked on dashboard"
  // Find the first create outcome that mentions a key matching the Epic project
  // (KAHOOT) and the "+ tracked" marker which Epics get and stories don't.
  const epicOutcome = outcomes.find(
    (o) => o.kind === "create" && o.ok && / \+ tracked on dashboard/i.test(o.detail || ""),
  );
  if (!epicOutcome) return;
  const match = (epicOutcome.detail || "").match(/created\s+([A-Z][A-Z0-9_]*-\d+)/i);
  if (!match) return;
  const newKey = match[1];

  try {
    await fetch(`/api/tracked/${encodeURIComponent(newKey)}/metadata`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        one_pager_url: state.fromIdea.one_pager_url,
        stakeholder: state.fromIdea.stakeholder,
        idea_id: state.fromIdea.id,
        documents: state.fromIdea.documents || [],
      }),
    });
  } catch {}
  try {
    await fetch(`/api/ideas/${encodeURIComponent(state.fromIdea.id)}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ promoted_epic_key: newKey }),
    });
  } catch {}
  state.fromIdea = null;
}

$("generate-btn").addEventListener("click", generatePlan);
$("apply-btn").addEventListener("click", applyChanges);

// ---------- Settings ----------

async function loadSettings() {
  try {
    const resp = await fetch("/api/settings");
    if (!resp.ok) throw new Error(await resp.text());
    const data = await resp.json();

    document.querySelector('input[name="ATLASSIAN_DOMAIN"]').value = data.ATLASSIAN_DOMAIN || "";
    document.querySelector('input[name="ATLASSIAN_EMAIL"]').value = data.ATLASSIAN_EMAIL || "";

    ["ATLASSIAN_API_TOKEN", "ANTHROPIC_API_KEY", "FIGMA_API_TOKEN"].forEach((k) => {
      const pill = $(`status-${k}`);
      if (data[k] && data[k].set) {
        pill.textContent = data[k].preview ? `set · ${data[k].preview}` : "set";
        pill.classList.add("set");
      } else {
        pill.textContent = "not set";
        pill.classList.remove("set");
      }
    });
  } catch (e) {
    $("settings-status").className = "status error";
    $("settings-status").textContent = `failed to load: ${e.message}`;
  }
}

$("settings-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const form = e.target;
  const updates = {};
  for (const el of form.querySelectorAll("input")) {
    if (el.value.trim()) updates[el.name] = el.value.trim();
  }
  if (Object.keys(updates).length === 0) {
    $("settings-status").className = "status";
    $("settings-status").textContent = "nothing to save (all inputs empty)";
    return;
  }

  $("settings-status").className = "status";
  $("settings-status").textContent = "saving…";
  try {
    const resp = await fetch("/api/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(updates),
    });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({ detail: resp.statusText }));
      throw new Error(err.detail || resp.statusText);
    }
    // Clear secret inputs after save so they don't sit visible
    form.querySelectorAll('input[type="password"]').forEach((i) => (i.value = ""));
    await loadSettings();
    $("settings-status").textContent = `saved (${Object.keys(updates).join(", ")})`;
  } catch (e) {
    $("settings-status").className = "status error";
    $("settings-status").textContent = `error: ${e.message}`;
  }
});

$("settings-reveal").addEventListener("click", () => {
  const btn = $("settings-reveal");
  const pwd = document.querySelectorAll('#settings-form input[type="password"]');
  const txt = document.querySelectorAll('#settings-form input.revealed');
  if (pwd.length) {
    pwd.forEach((i) => { i.type = "text"; i.classList.add("revealed"); });
    btn.textContent = "Hide inputs";
  } else {
    txt.forEach((i) => { i.type = "password"; i.classList.remove("revealed"); });
    btn.textContent = "Reveal inputs";
  }
});

// ---------- Company config (app_config table) ----------

async function loadAppConfig() {
  try {
    const resp = await fetch("/api/config");
    if (!resp.ok) return;
    const cfg = await resp.json();
    $("cfg-company-name").value = cfg.company_name || "";
    $("cfg-fe-project").value = cfg.fe_project_key || "";
    $("cfg-be-project").value = cfg.be_project_key || "";
    $("cfg-required-label").value = cfg.required_label || "";
    $("cfg-business-context").value = cfg.business_context || "";
  } catch {}
}

async function saveAppConfig() {
  $("cfg-status").className = "status";
  $("cfg-status").textContent = "saving…";
  try {
    const resp = await fetch("/api/config", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        company_name: $("cfg-company-name").value.trim(),
        fe_project_key: $("cfg-fe-project").value.trim(),
        be_project_key: $("cfg-be-project").value.trim(),
        required_label: $("cfg-required-label").value.trim(),
        business_context: $("cfg-business-context").value,
      }),
    });
    if (!resp.ok) throw new Error(resp.statusText);
    $("cfg-status").textContent = "saved";
    setTimeout(() => { $("cfg-status").textContent = ""; }, 2500);
  } catch (e) {
    $("cfg-status").className = "status error";
    $("cfg-status").textContent = `error: ${e.message}`;
  }
}

$("cfg-save-btn")?.addEventListener("click", saveAppConfig);


// ---------- Google Drive connect ----------

async function refreshGoogleStatus() {
  try {
    const resp = await fetch("/api/google/status");
    if (!resp.ok) return;
    const s = await resp.json();
    const pill = $("google-status-pill");
    if (!s.configured) {
      pill.textContent = "needs setup";
      pill.className = "status-pill not-configured";
    } else if (s.connected) {
      pill.textContent = "connected";
      pill.className = "status-pill connected";
    } else {
      pill.textContent = "not connected";
      pill.className = "status-pill disconnected";
    }
    $("google-connect-btn").classList.toggle("hidden", !s.configured || s.connected);
    $("google-disconnect-btn").classList.toggle("hidden", !s.connected);
  } catch {}
}

async function connectGoogle() {
  try {
    const resp = await fetch("/api/google/auth-url");
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({ detail: resp.statusText }));
      throw new Error(err.detail || resp.statusText);
    }
    const { url } = await resp.json();
    const popup = window.open(url, "google-oauth", "width=560,height=720");
    $("google-status").className = "status";
    $("google-status").textContent = "waiting for Google consent…";
    // Poll for completion - the popup closes itself on success.
    const interval = setInterval(async () => {
      if (popup?.closed) {
        clearInterval(interval);
        await refreshGoogleStatus();
        $("google-status").textContent = "connect flow finished";
        setTimeout(() => { $("google-status").textContent = ""; }, 3000);
      }
    }, 700);
  } catch (e) {
    $("google-status").className = "status error";
    $("google-status").textContent = `error: ${e.message}`;
  }
}

async function disconnectGoogle() {
  if (!confirm("Disconnect Google Drive? Cached doc content will stay; new fetches will fail until reconnected.")) return;
  try {
    const resp = await fetch("/api/google/disconnect", { method: "POST" });
    if (!resp.ok) throw new Error(resp.statusText);
    await refreshGoogleStatus();
  } catch (e) {
    $("google-status").className = "status error";
    $("google-status").textContent = `error: ${e.message}`;
  }
}

function openGoogleSetupHelp() {
  // Fill the redirect URI to match this exact host:port - what the user must paste into Google Cloud.
  const uri = `${window.location.origin}/api/google/callback`;
  $("google-redirect-uri").textContent = uri;
  $("google-setup-modal").classList.remove("hidden");
}
function closeGoogleSetupHelp() {
  $("google-setup-modal").classList.add("hidden");
}

$("google-connect-btn")?.addEventListener("click", connectGoogle);
$("google-disconnect-btn")?.addEventListener("click", disconnectGoogle);
$("google-setup-help")?.addEventListener("click", openGoogleSetupHelp);
$("google-setup-close")?.addEventListener("click", closeGoogleSetupHelp);
$("google-setup-done")?.addEventListener("click", closeGoogleSetupHelp);
// Backdrop click closes
$("google-setup-modal")?.addEventListener("click", (e) => {
  if (e.target.id === "google-setup-modal") closeGoogleSetupHelp();
});

// ---------- Status page ----------

const statusState = {
  entries: [],
  detail: null,
  currentKey: null,
  userEmail: null,
  atlassianDomain: null,
  authenticated: false,
  authMode: "password",  // "password" | "open"
  lastSynced: null,
  showDismissedRisks: false,
  showDismissedGaps: false,
  showDismissedRecommendations: false,
  showDoneActions: false,
  extractedActions: null, // {proposed: [...], notes: "..."} - preview state
  // When set, holds the result of a background refresh that we haven't applied
  // to the visible UI yet - the user clicks Refresh in the banner to swap.
  pendingRefresh: null,
  bgRefreshInFlight: false,
  // Same idea but for the detail view (LLM analysis can change between visits).
  pendingDetailRefresh: null,
  // Inline forms open for a specific item: {type:"risk|gap|action", sig:"..."}.
  // Only one at a time to keep the UI quiet.
  openClosure: null,
  openCreateFromItem: null,
  // Ticket-list sorting (Status detail view)
  ticketsSortBy: "status",
  ticketsSortDir: "asc",
};

const JIRA_KEY_RE = /^[A-Z][A-Z0-9_]*-\d+$/;

function isJiraKey(s) {
  return typeof s === "string" && JIRA_KEY_RE.test(s);
}

function jiraUrl(key) {
  if (!statusState.atlassianDomain || !isJiraKey(key)) return null;
  return `https://${statusState.atlassianDomain}/browse/${encodeURIComponent(key)}`;
}

// Stub-ticket creation: server creates a "CHANGE ME" placeholder under the
// epic (label Commercial, auto-transitioned to Selected for Development) and
// returns the new ticket's URL, which we open in a new tab for the user to
// edit. More reliable than Jira's URL-param create modal which silently
// ignores parent/labels.
async function createStubTicket(epicKey, btnEl) {
  if (!isJiraKey(epicKey)) return;
  const originalText = btnEl?.textContent;
  if (btnEl) {
    btnEl.disabled = true;
    btnEl.textContent = "Creating…";
  }
  try {
    const resp = await fetch(`/api/tracked/${encodeURIComponent(epicKey)}/create-stub`, { method: "POST" });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({ detail: resp.statusText }));
      throw new Error(err.detail || resp.statusText);
    }
    const { url, key } = await resp.json();
    if (url) window.open(url, "_blank", "noopener,noreferrer");
    // Bring the dashboard row in sync (counts changed) - no banner, just a quiet refetch.
    refreshOneRow(epicKey).catch(() => {});
    return key;
  } catch (e) {
    alert(`Create failed: ${e.message}`);
  } finally {
    if (btnEl) {
      btnEl.disabled = false;
      btnEl.textContent = originalText;
    }
  }
}

function jiraLinkIcon(key, label) {
  const url = jiraUrl(key);
  if (!url) return "";
  // External-link arrow icon (inline SVG so we don't need an icon font)
  return `
    <a class="jira-link" href="${url}" target="_blank" rel="noopener noreferrer"
       title="Open ${escapeHtml(label || key)} in Jira"
       aria-label="Open ${escapeHtml(label || key)} in Jira"
       onclick="event.stopPropagation()">
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
        <path d="M10 6H6a2 2 0 0 0-2 2v10a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-4"/>
        <polyline points="15 3 21 3 21 9"/>
        <line x1="10" y1="14" x2="21" y2="3"/>
      </svg>
    </a>
  `;
}

// Render a Jira key alongside its link icon. Use everywhere a key shows up in
// the UI. Returns just the escaped string when the value isn't a real Jira key
// (e.g. proposal temp_ids like "new-1"), so the helper is safe to apply blindly.
function ticketKey(k, label) {
  return `${escapeHtml(k || "")}${jiraLinkIcon(k, label)}`;
}

// URL <-> tab mapping. Paths match the visible tab labels so the URL is
// readable; the internal data-page IDs stay as sync/status/slack/settings.
const PAGE_TO_PATH = {
  status: "/projects",
  ppr: "/ppr",
  actions: "/actions",
  ideas: "/ideas",
  sync: "/requirements",
  slack: "/slack",
  settings: "/settings",
};
const PATH_TO_PAGE = Object.fromEntries(
  Object.entries(PAGE_TO_PATH).map(([k, v]) => [v, k]),
);
PATH_TO_PAGE["/"] = "status";

function pageFromPath(pathname) {
  return PATH_TO_PAGE[pathname] || "status";
}

function showPage(page, { pushHistory = true } = {}) {
  document.querySelectorAll("#main-nav .nav-btn").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.page === page);
  });
  $("page-sync").classList.toggle("hidden", page !== "sync");
  $("page-status").classList.toggle("hidden", page !== "status");
  $("page-ppr").classList.toggle("hidden", page !== "ppr");
  $("page-actions").classList.toggle("hidden", page !== "actions");
  $("page-ideas").classList.toggle("hidden", page !== "ideas");
  $("page-slack").classList.toggle("hidden", page !== "slack");
  $("page-settings").classList.toggle("hidden", page !== "settings");
  if (page === "settings") { loadSettings(); loadTeam(); refreshGoogleStatus(); loadAppConfig(); }
  if (page === "status") loadStatusList();
  if (page === "ppr") loadPPR();
  if (page === "actions") loadActions();
  if (page === "ideas") loadIdeas();

  const targetPath = PAGE_TO_PATH[page] || "/";
  if (pushHistory && window.location.pathname !== targetPath) {
    history.pushState({ page }, "", targetPath);
  }
}

// Re-bind nav clicks (replace prior listeners by re-querying - idempotent enough)
document.querySelectorAll("#main-nav .nav-btn").forEach((btn) => {
  btn.replaceWith(btn.cloneNode(true));
});
document.querySelectorAll("#main-nav .nav-btn").forEach((btn) => {
  btn.addEventListener("click", () => showPage(btn.dataset.page));
});

// Back/forward navigation -> apply URL without pushing a new state
window.addEventListener("popstate", () => {
  showPage(pageFromPath(window.location.pathname), { pushHistory: false });
});

// Initial page = whatever the URL says (handles refresh and deep links)
showPage(pageFromPath(window.location.pathname), { pushHistory: false });

function daysBetween(isoA, isoB) {
  const a = new Date(isoA);
  const b = new Date(isoB);
  return Math.floor((b - a) / (1000 * 60 * 60 * 24));
}

function ticketRowClass(t) {
  const classes = [];
  const today = new Date().toISOString().slice(0, 10);
  if (t.duedate && t.status_category !== "done") {
    if (t.duedate < today) classes.push("overdue");
    else if (daysBetween(today, t.duedate) <= 7) classes.push("due-soon");
  }
  if (t.updated) {
    const age = daysBetween(t.updated.slice(0, 10), today);
    if (age > 21) classes.push("stale");
  }
  if (t.assignee_email && statusState.userEmail && t.assignee_email.toLowerCase() === statusState.userEmail.toLowerCase()) {
    classes.push("for-you-row");
  }
  return classes.join(" ");
}

function progressBar(counts) {
  const total = counts.total || 1;
  const segs = [
    { cls: "done", pct: (counts.done / total) * 100 },
    { cls: "in-progress", pct: (counts.in_progress / total) * 100 },
    { cls: "to-do", pct: (counts.to_do / total) * 100 },
  ];
  return `
    <div class="progress-bar">
      ${segs.map((s) => s.pct > 0 ? `<div class="${s.cls}" style="width:${s.pct}%"></div>` : "").join("")}
    </div>
    <div class="progress-legend">
      <span class="done">${counts.done} done</span>
      <span class="in-progress">${counts.in_progress} in progress</span>
      <span class="to-do">${counts.to_do} to do</span>
      <span style="color:#6b778c">(${counts.total} total)</span>
    </div>
  `;
}

const ROLE_DISPLAY = {
  backend: { label: "Backend", cls: "role-backend" },
  frontend: { label: "Frontend", cls: "role-frontend" },
  design: { label: "Design", cls: "role-design" },
  other: { label: "Other", cls: "role-other" },
  unassigned: { label: "Unassigned", cls: "role-unassigned" },
};

function roleSplitRow(split, options = {}) {
  // Compact per-role progress for use under the main progress bar.
  // options.compact = true → cards (smaller text); false → detail view.
  if (!split) return "";
  const roles = ["backend", "frontend", "design", "other", "unassigned"];
  const visible = roles.filter((r) => (split[r]?.total || 0) > 0);
  if (!visible.length) return "";
  const compact = options.compact !== false;
  return `
    <div class="role-split ${compact ? "compact" : ""}">
      ${visible.map((r) => {
        const c = split[r];
        const total = c.total || 1;
        const donePct = (c.done / total) * 100;
        const ipPct = (c.in_progress / total) * 100;
        const tdPct = (c.to_do / total) * 100;
        const meta = ROLE_DISPLAY[r];
        const label = compact
          ? `<span class="role-label ${meta.cls}">${meta.label} ${c.done}/${c.total}</span>`
          : `<span class="role-label ${meta.cls}">${meta.label}</span><span class="role-count">${c.done} / ${c.total} done${c.in_progress ? `, ${c.in_progress} in progress` : ""}</span>`;
        return `
          <div class="role-row">
            ${label}
            <div class="role-bar">
              ${donePct > 0 ? `<div class="done" style="width:${donePct}%"></div>` : ""}
              ${ipPct > 0 ? `<div class="in-progress" style="width:${ipPct}%"></div>` : ""}
              ${tdPct > 0 ? `<div class="to-do" style="width:${tdPct}%"></div>` : ""}
            </div>
          </div>
        `;
      }).join("")}
    </div>
  `;
}

function relativeTime(iso) {
  if (!iso) return "never";
  const then = new Date(iso);
  const diffSec = Math.floor((Date.now() - then.getTime()) / 1000);
  if (diffSec < 60) return "just now";
  if (diffSec < 3600) return `${Math.floor(diffSec / 60)}m ago`;
  if (diffSec < 86400) return `${Math.floor(diffSec / 3600)}h ago`;
  return `${Math.floor(diffSec / 86400)}d ago`;
}

function freshnessClass(iso) {
  if (!iso) return "stale";
  const ageMin = (Date.now() - new Date(iso).getTime()) / 1000 / 60;
  if (ageMin < 10) return "fresh";
  if (ageMin < 60) return "";
  return "stale";
}

function renderDashboardLastSynced() {
  const el = $("dashboard-last-synced");
  if (!el) return;
  if (!statusState.lastSynced) {
    el.textContent = "not synced yet";
    el.className = "sync-time stale";
    return;
  }
  el.textContent = `last sync: ${relativeTime(statusState.lastSynced)}`;
  el.className = `sync-time ${freshnessClass(statusState.lastSynced)}`;
  el.title = new Date(statusState.lastSynced).toLocaleString();
}

async function loadStatusList(forceRefresh = false) {
  $("track-status").className = "status";
  $("track-status").textContent = forceRefresh ? "refreshing from Jira…" : "loading…";
  try {
    // Fetch user email + atlassian domain from settings (lazy, once per session)
    if (!statusState.userEmail || !statusState.atlassianDomain) {
      const settingsResp = await fetch("/api/settings");
      if (settingsResp.ok) {
        const s = await settingsResp.json();
        statusState.userEmail = statusState.userEmail || s.ATLASSIAN_EMAIL || null;
        statusState.atlassianDomain = statusState.atlassianDomain || s.ATLASSIAN_DOMAIN || null;
      }
    }

    const url = forceRefresh ? "/api/tracked/refresh-all" : "/api/tracked";
    const resp = await fetch(url, { method: forceRefresh ? "POST" : "GET" });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({ detail: resp.statusText }));
      throw new Error(err.detail || resp.statusText);
    }
    const data = await resp.json();
    statusState.entries = data.entries || [];
    statusState.lastSynced = data.last_synced || null;
    // A manual refresh-all invalidates any pending background result.
    if (forceRefresh) statusState.pendingRefresh = null;
    renderDashboardLastSynced();
    renderStatusList();
    $("track-status").textContent = statusState.entries.length === 0 ? "no epics tracked yet" : "";

    // After serving cached data, kick off a background refresh so the user
    // can see (and opt into) any newer state. Skip when we just did a manual
    // refresh, when there's nothing to refresh, when the user is anonymous
    // (write endpoint), or when one is already in flight.
    if (
      !forceRefresh
      && statusState.authenticated
      && statusState.entries.length > 0
      && !statusState.bgRefreshInFlight
    ) {
      kickBackgroundRefresh();
    }
  } catch (e) {
    $("track-status").className = "status error";
    $("track-status").textContent = `error: ${e.message}`;
  }
}

// Fingerprint of a single entry for change detection. Anything that affects
// the visible card is included; metadata changes (saved via the UI itself)
// aren't part of this since the user already saw them happen.
function _entryFingerprint(e) {
  const c = e.counts || {};
  return JSON.stringify({
    k: e.key,
    s: e.status,
    sc: e.status_category,
    p: e.progress_pct,
    a: e.assessment,
    td: c.to_do, ip: c.in_progress, dn: c.done, t: c.total,
    ls: e.last_synced,
  });
}

function _entriesDiffer(a, b) {
  if (a.length !== b.length) return true;
  const fa = a.map(_entryFingerprint).sort();
  const fb = b.map(_entryFingerprint).sort();
  for (let i = 0; i < fa.length; i += 1) {
    if (fa[i] !== fb[i]) return true;
  }
  return false;
}

async function kickBackgroundRefresh() {
  if (statusState.bgRefreshInFlight) return;
  statusState.bgRefreshInFlight = true;
  // Subtle hint that something's happening in the background.
  const indicator = $("dashboard-last-synced");
  const originalLabel = indicator?.textContent || "";
  if (indicator) {
    indicator.classList.add("loading");
    indicator.innerHTML = `<span class="spinner"></span> checking for updates…`;
  }
  try {
    const resp = await fetch("/api/tracked/refresh-all", { method: "POST" });
    if (!resp.ok) return;  // silent - the user can manually refresh if they want
    const data = await resp.json();
    const fresh = data.entries || [];
    if (_entriesDiffer(statusState.entries, fresh)) {
      statusState.pendingRefresh = { entries: fresh, last_synced: data.last_synced || null };
      renderPendingRefreshBanner();
    } else {
      // Same data - just update the last-synced timestamp silently.
      statusState.lastSynced = data.last_synced || statusState.lastSynced;
      renderDashboardLastSynced();
    }
  } catch {
    // Network blip; ignore.
  } finally {
    statusState.bgRefreshInFlight = false;
    if (indicator && !statusState.pendingRefresh) {
      indicator.classList.remove("loading");
      renderDashboardLastSynced();
    }
  }
}

function renderPendingRefreshBanner() {
  const host = $("tracked-list");
  if (!host) return;
  // Remove any previous banner before inserting a new one
  document.getElementById("refresh-banner")?.remove();
  if (!statusState.pendingRefresh) return;
  const banner = document.createElement("div");
  banner.id = "refresh-banner";
  banner.className = "refresh-banner";
  banner.innerHTML = `
    <span class="refresh-banner-text">Fresh data available from Jira.</span>
    <div class="refresh-banner-actions">
      <button type="button" data-action="apply-pending-refresh">Show fresh</button>
      <button type="button" class="secondary" data-action="dismiss-pending-refresh">Dismiss</button>
    </div>
  `;
  host.parentNode.insertBefore(banner, host);
}

function applyPendingRefresh() {
  if (!statusState.pendingRefresh) return;
  statusState.entries = statusState.pendingRefresh.entries;
  statusState.lastSynced = statusState.pendingRefresh.last_synced;
  statusState.pendingRefresh = null;
  document.getElementById("refresh-banner")?.remove();
  renderDashboardLastSynced();
  renderStatusList();
}

function dismissPendingRefresh() {
  statusState.pendingRefresh = null;
  document.getElementById("refresh-banner")?.remove();
  renderDashboardLastSynced();
}

async function refreshOneRow(key) {
  const card = document.querySelector(`.epic-card[data-key="${key}"]`);
  if (card) card.classList.add("refreshing");
  try {
    const resp = await fetch(`/api/tracked/${encodeURIComponent(key)}/refresh-row`, { method: "POST" });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({ detail: resp.statusText }));
      throw new Error(err.detail || resp.statusText);
    }
    const entry = await resp.json();
    const idx = statusState.entries.findIndex((e) => e.key === key);
    if (idx >= 0) statusState.entries[idx] = entry;
    renderStatusList();
  } catch (e) {
    alert(`Refresh ${key} failed: ${e.message}`);
  } finally {
    if (card) card.classList.remove("refreshing");
  }
}

function renderStatusList() {
  const container = $("tracked-list");
  container.innerHTML = statusState.entries
    .map((e) => {
      const today = new Date().toISOString().slice(0, 10);
      let dueState = "";
      if (e.duedate && e.duedate < today) dueState = "overdue";
      else if (e.duedate && daysBetween(today, e.duedate) <= 7) dueState = "due-soon";
      const health = cardHealth(e);
      return `
      <div class="epic-card health-${health.cls} ${statusState.currentKey === e.key ? "active" : ""}" data-action="view" data-key="${escapeHtml(e.key)}" draggable="true">
        <div class="card-head">
          <div class="card-key-line">
            <span class="key">${escapeHtml(e.key)}</span>
            ${jiraLinkIcon(e.key, e.summary)}
          </div>
          <div class="card-head-right">
            <span class="health-chip health-${health.cls}">${escapeHtml(health.label)}</span>
            ${isJiraKey(e.key) ? `<button class="icon-btn" data-action="create-stub" data-key="${escapeHtml(e.key)}" title="Create a stub ticket under this epic and open it in Jira">+</button>` : ""}
            <button class="icon-btn" data-action="refresh-row" data-key="${escapeHtml(e.key)}" title="Refetch from Jira">↻</button>
          </div>
        </div>
        <div class="card-summary">${escapeHtml(e.summary)}</div>
        <div class="card-meta">
          <span class="progress-pct">${e.progress_pct}%</span>
          ${(typeof e.open_action_count === "number" && e.open_action_count > 0) ? `<span class="action-count" title="Open action items">${e.open_action_count} action${e.open_action_count === 1 ? "" : "s"}</span>` : ""}
          ${(typeof e.for_user_action_count === "number" && e.for_user_action_count > 0) ? `<span class="action-count action-count-for-you" title="Action items flagged for you">${e.for_user_action_count} for you</span>` : ""}
          ${e.duedate ? `<span class="card-due ${dueState}">${escapeHtml(e.duedate)}</span>` : ""}
          ${e.metadata?.stakeholder ? `<span class="idea-stakeholder">${escapeHtml(e.metadata.stakeholder)}</span>` : ""}
          ${(e.metadata?.segments || []).map((s) => `<span class="segment-chip segment-${escapeHtml(s)}">${escapeHtml(s)}</span>`).join("")}
          ${e.metadata?.one_pager_url ? `<a class="idea-onepager" href="${escapeHtml(e.metadata.one_pager_url)}" target="_blank" rel="noopener noreferrer" onclick="event.stopPropagation()">one-pager ↗</a>` : ""}
          ${e.last_synced ? `<span class="sync-time-inline">${escapeHtml(relativeTime(e.last_synced))}</span>` : ""}
        </div>
        ${progressBar(e.counts)}
        ${roleSplitRow(e.role_split, { compact: true })}
        <div class="card-footer">
          <button class="link-btn" data-action="remove" data-key="${escapeHtml(e.key)}">remove</button>
        </div>
      </div>
      `;
    })
    .join("");
}

// Card health. Prefers the LLM assessment from the server (so the card matches
// what the detail view shows) and falls back to a heuristic when no analysis
// has run yet. Both are mapped to the same 5-class colour scheme.
function cardHealth(e) {
  // 1. LLM assessment (authoritative once computed)
  if (e.assessment) {
    const cls = ASSESSMENT_TO_HEALTH[e.assessment] || "unknown";
    return { cls, label: ASSESSMENT_LABEL[e.assessment] || e.assessment };
  }
  // 2. Heuristic fallback - only used until the user opens the epic at least once
  const today = new Date().toISOString().slice(0, 10);
  const total = e.counts?.total || 0;
  if (total === 0) return { cls: "unknown", label: "no data" };
  if (e.progress_pct === 100) return { cls: "done", label: "done" };
  if (e.duedate && e.duedate < today) return { cls: "behind", label: "behind" };
  if (e.duedate && daysBetween(today, e.duedate) <= 7 && e.progress_pct < 70) {
    return { cls: "at-risk", label: "at risk" };
  }
  if (e.duedate && daysBetween(today, e.duedate) <= 14 && e.progress_pct < 40) {
    return { cls: "at-risk", label: "at risk" };
  }
  if (e.progress_pct === 0) return { cls: "unknown", label: "not started" };
  return { cls: "ok", label: "on track" };
}

function sortHeader(col, label) {
  const active = statusState.ticketsSortBy === col;
  const arrow = active ? (statusState.ticketsSortDir === "asc" ? "↑" : "↓") : "";
  return `<div class="sort-header ${active ? "active" : ""}" data-action="sort-tickets" data-col="${col}">${escapeHtml(label)} <span class="sort-arrow">${arrow}</span></div>`;
}

function sortTickets(tickets) {
  const by = statusState.ticketsSortBy;
  const dir = statusState.ticketsSortDir === "desc" ? -1 : 1;
  // For status, prefer ordering by category (in-progress before to-do before done) then by status name.
  const statusOrder = { indeterminate: 0, new: 1, done: 2, unknown: 3 };
  const accessor = (t) => {
    if (by === "key") {
      // Sort by numeric suffix to get natural order, falling back to string.
      const m = (t.key || "").match(/-(\d+)$/);
      return m ? parseInt(m[1], 10) : (t.key || "");
    }
    if (by === "status") {
      return [statusOrder[t.status_category] ?? 4, (t.status || "").toLowerCase()];
    }
    if (by === "assignee") {
      return (t.assignee || "~~unassigned").toLowerCase();
    }
    return "";
  };
  return [...tickets].sort((a, b) => {
    const va = accessor(a);
    const vb = accessor(b);
    if (Array.isArray(va)) {
      for (let i = 0; i < va.length; i += 1) {
        if (va[i] < vb[i]) return -1 * dir;
        if (va[i] > vb[i]) return 1 * dir;
      }
      return 0;
    }
    if (va < vb) return -1 * dir;
    if (va > vb) return 1 * dir;
    return 0;
  });
}

const ASSESSMENT_TO_HEALTH = {
  "on-track": "ok",
  "ahead": "ok",
  "at-risk": "at-risk",
  "behind": "behind",
  "stalled": "behind",
  "unknown": "unknown",
};
const ASSESSMENT_LABEL = {
  "on-track": "on track",
  "ahead": "ahead",
  "at-risk": "at risk",
  "behind": "behind",
  "stalled": "stalled",
  "unknown": "unknown",
};

// ---- Drag-drop reorder of project cards ----

let _projectDragKey = null;

function onCardDragStart(e) {
  // Don't start a card drag when the user is grabbing a button / link inside it.
  if (e.target.closest("button, a, input, select, textarea")) {
    e.preventDefault();
    return;
  }
  const card = e.target.closest(".epic-card");
  if (!card) return;
  _projectDragKey = card.dataset.key;
  card.classList.add("dragging");
  e.dataTransfer.effectAllowed = "move";
  e.dataTransfer.setData("text/plain", _projectDragKey);
}

function onCardDragEnd(e) {
  e.target.closest(".epic-card")?.classList.remove("dragging");
  document.querySelectorAll(".epic-card.drop-target").forEach((el) => el.classList.remove("drop-target"));
  _projectDragKey = null;
}

function onListDragOver(e) {
  if (!_projectDragKey) return;
  e.preventDefault();
  e.dataTransfer.dropEffect = "move";
  // Highlight the card we'd drop in front of
  document.querySelectorAll(".epic-card.drop-target").forEach((el) => el.classList.remove("drop-target"));
  const target = e.target.closest(".epic-card");
  if (target && target.dataset.key !== _projectDragKey) target.classList.add("drop-target");
}

async function onListDrop(e) {
  if (!_projectDragKey) return;
  e.preventDefault();
  const target = e.target.closest(".epic-card");
  const dragged = _projectDragKey;
  const cards = [...$("tracked-list").querySelectorAll(".epic-card")];
  // Compute the new ordering of keys
  const keys = cards.map((c) => c.dataset.key).filter((k) => k !== dragged);
  let insertIdx = keys.length;
  if (target && target.dataset.key !== dragged) {
    const tIdx = keys.indexOf(target.dataset.key);
    if (tIdx >= 0) {
      const rect = target.getBoundingClientRect();
      // Drop before target if cursor is in its top half, after otherwise.
      insertIdx = e.clientY < rect.top + rect.height / 2 ? tIdx : tIdx + 1;
    }
  }
  keys.splice(insertIdx, 0, dragged);
  document.querySelectorAll(".epic-card.drop-target").forEach((el) => el.classList.remove("drop-target"));
  _projectDragKey = null;

  // Optimistically reorder locally + persist server-side
  const byKey = Object.fromEntries(statusState.entries.map((e) => [e.key, e]));
  statusState.entries = keys.map((k) => byKey[k]).filter(Boolean);
  renderStatusList();
  try {
    const resp = await fetch("/api/tracked/reorder", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ keys }),
    });
    if (!resp.ok) throw new Error(resp.statusText);
  } catch (err) {
    alert(`Reorder failed: ${err.message}`);
    loadStatusList();  // re-sync from server
  }
}

$("tracked-list").addEventListener("dragstart", onCardDragStart);
$("tracked-list").addEventListener("dragend", onCardDragEnd);
$("tracked-list").addEventListener("dragover", onListDragOver);
$("tracked-list").addEventListener("drop", onListDrop);

async function trackEpic() {
  const key = $("track-key").value.trim();
  if (!key) return;
  $("track-status").className = "status";
  $("track-status").textContent = "adding + fetching from Jira…";
  try {
    const resp = await fetch("/api/tracked", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ key }),
    });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({ detail: resp.statusText }));
      throw new Error(err.detail || resp.statusText);
    }
    // Server returns the full dashboard (with the new row populated) - render directly
    const data = await resp.json();
    statusState.entries = data.entries || [];
    statusState.lastSynced = data.last_synced || statusState.lastSynced;
    renderDashboardLastSynced();
    renderStatusList();
    $("track-key").value = "";
    $("track-status").textContent = "added";
    setTimeout(() => { $("track-status").textContent = ""; }, 2000);
  } catch (e) {
    $("track-status").className = "status error";
    $("track-status").textContent = `error: ${e.message}`;
  }
}

async function removeTracked(key) {
  if (!confirm(`Stop tracking ${key}?`)) return;
  try {
    await fetch(`/api/tracked/${encodeURIComponent(key)}`, { method: "DELETE" });
    if (statusState.currentKey === key) {
      statusState.currentKey = null;
      $("status-detail-section").classList.add("hidden");
    }
    await loadStatusList();
  } catch (e) {
    alert(`Failed to remove: ${e.message}`);
  }
}

// Persist the last-seen detail per epic in the server-side detail_cache table
// so any client / new session sees the previous state immediately while a
// fresh fetch runs in the background. Replaces the earlier localStorage-only
// approach.
async function loadCachedDetail(key) {
  try {
    const resp = await fetch(`/api/tracked/${encodeURIComponent(key)}/detail-cache`);
    if (!resp.ok) return null;
    const data = await resp.json();
    return data && data.detail ? data.detail : null;
  } catch { return null; }
}

async function saveCachedDetail(key, detail) {
  try {
    await fetch(`/api/tracked/${encodeURIComponent(key)}/detail-cache`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(detail),
    });
  } catch {
    // non-fatal
  }
}

function _detailFingerprint(d) {
  if (!d) return "";
  const a = d.analysis || {};
  return JSON.stringify({
    cs: d.counts,
    pp: d.progress_pct,
    pa: a.progress_assessment,
    sop: a.state_of_play,
    actions: (a.action_items || []).map((x) => ({ s: x.sig, d: x.done, t: x.title })),
    risks: (a.risks || []).map((x) => ({ s: x.sig, d: x.dismissed, t: x.title })),
    gaps: (a.gaps || []).map((x) => ({ s: x.sig, d: x.dismissed, t: x.title })),
    recs: (a.recommendations || []).map((x) => x.title),
    md: d.metadata,
    rs: d.role_split,
  });
}

async function viewDetail(key, forceRefresh = false) {
  statusState.currentKey = key;
  renderStatusList();
  $("status-detail-section").classList.remove("hidden");

  const cached = forceRefresh ? null : await loadCachedDetail(key);
  if (cached) {
    // Render the cached version immediately so the user sees something useful.
    statusState.detail = cached;
    statusState.pendingDetailRefresh = null;
    renderDetail();
    showDetailFreshnessIndicator("checking for updates…");
  } else {
    statusState.detail = null;
    $("status-detail").innerHTML = `<p class="status">${forceRefresh ? "refreshing analysis…" : "loading…"}</p>`;
    // Fire the fast (no-LLM) path in parallel so the header/tickets/progress
    // show up immediately while the LLM analysis is still running below.
    if (!forceRefresh) {
      fetch(`/api/tracked/${encodeURIComponent(key)}/basic`)
        .then((r) => (r.ok ? r.json() : null))
        .then((basic) => {
          // Only apply if the user hasn't navigated away and the full detail
          // hasn't already arrived.
          if (!basic) return;
          if (statusState.currentKey !== key) return;
          if (statusState.detail) return;
          statusState.detail = basic;
          renderDetail();
          if (!basic.analysis) showDetailFreshnessIndicator("running analysis (30-60s)…");
        })
        .catch(() => { /* non-fatal */ });
    }
  }

  try {
    const url = forceRefresh
      ? `/api/tracked/${encodeURIComponent(key)}/refresh`
      : `/api/tracked/${encodeURIComponent(key)}`;
    const resp = await fetch(url, { method: forceRefresh ? "POST" : "GET" });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({ detail: resp.statusText }));
      throw new Error(err.detail || resp.statusText);
    }
    const fresh = await resp.json();

    // Push the assessment onto the matching dashboard entry so the card chip
    // matches the detail view's progress_assessment without a round-trip.
    const idx = statusState.entries.findIndex((e) => e.key === key);
    if (idx >= 0 && fresh.analysis?.progress_assessment) {
      statusState.entries[idx] = {
        ...statusState.entries[idx],
        assessment: fresh.analysis.progress_assessment,
      };
      renderStatusList();
    }

    if (cached && _detailFingerprint(cached) !== _detailFingerprint(fresh)) {
      // Different - stash and offer a swap.
      statusState.pendingDetailRefresh = fresh;
      saveCachedDetail(key, fresh);
      renderPendingDetailBanner();
    } else {
      // Either no cached version (first load) or same content - swap in place.
      statusState.detail = fresh;
      statusState.pendingDetailRefresh = null;
      saveCachedDetail(key, fresh);
      if (!cached) renderDetail();
      clearDetailFreshnessIndicator();
    }
  } catch (e) {
    if (cached) {
      // Background fetch failed - we already have a cached view, just clear the indicator.
      clearDetailFreshnessIndicator(`(refresh failed: ${e.message})`);
    } else {
      $("status-detail").innerHTML = `<p class="status error">error: ${escapeHtml(e.message)}</p>`;
    }
  }
}

function showDetailFreshnessIndicator(text) {
  // Lightweight pill at the top of the detail section while bg fetch runs.
  let host = document.getElementById("detail-freshness");
  if (!host) {
    host = document.createElement("div");
    host.id = "detail-freshness";
    host.className = "status loading detail-freshness";
    $("status-detail").parentNode.insertBefore(host, $("status-detail"));
  }
  host.innerHTML = `<span class="spinner"></span> ${escapeHtml(text)}`;
}

function clearDetailFreshnessIndicator(finalText) {
  const host = document.getElementById("detail-freshness");
  if (!host) return;
  if (finalText) {
    host.className = "status";
    host.textContent = finalText;
    setTimeout(() => host.remove(), 4000);
  } else {
    host.remove();
  }
}

function renderPendingDetailBanner() {
  clearDetailFreshnessIndicator();
  document.getElementById("detail-refresh-banner")?.remove();
  if (!statusState.pendingDetailRefresh) return;
  const banner = document.createElement("div");
  banner.id = "detail-refresh-banner";
  banner.className = "refresh-banner";
  banner.innerHTML = `
    <span class="refresh-banner-text">Updated analysis available.</span>
    <div class="refresh-banner-actions">
      <button type="button" data-action="apply-pending-detail">Show fresh</button>
      <button type="button" class="secondary" data-action="dismiss-pending-detail">Dismiss</button>
    </div>
  `;
  $("status-detail").parentNode.insertBefore(banner, $("status-detail"));
}

function applyPendingDetail() {
  if (!statusState.pendingDetailRefresh) return;
  statusState.detail = statusState.pendingDetailRefresh;
  statusState.pendingDetailRefresh = null;
  document.getElementById("detail-refresh-banner")?.remove();
  renderDetail();
}

function dismissPendingDetail() {
  statusState.pendingDetailRefresh = null;
  document.getElementById("detail-refresh-banner")?.remove();
}

function renderDetail() {
  const d = statusState.detail;
  if (!d) return;
  const a = d.analysis;
  const analysisLoading = !a;
  const loadingBlock = (label) => `
    <div class="analysis-section analysis-loading">
      <h4>${label}</h4>
      <p class="status loading"><span class="spinner"></span> analyzing (typically 30-60s)…</p>
    </div>`;

  const actionItem = (item, manualIndex) => {
    const cls = ["analysis-item", `urgency-${item.urgency}`];
    if (item.for_user) cls.push("for-user");
    if (item.source === "manual") cls.push("manual");
    if (item.done) cls.push("action-done");
    const sig = item.sig || "";
    const closureOpen = sig && statusState.openClosure?.type === "action" && statusState.openClosure?.sig === sig;
    const createOpen = sig && statusState.openCreateFromItem?.type === "action" && statusState.openCreateFromItem?.sig === sig;
    let actionsHtml = "";
    if (item.done) {
      actionsHtml = `<button class="link-btn detail-link" data-action="reopen-action" data-sig="${escapeHtml(sig)}">reopen</button>`;
    } else if (sig) {
      const forMeLabel = item.for_user ? "not for me" : "for me";
      actionsHtml = `
        <button class="link-btn detail-link" data-action="open-close" data-type="action" data-sig="${escapeHtml(sig)}">mark done</button>
        <button class="link-btn detail-link" data-action="toggle-for-user" data-sig="${escapeHtml(sig)}" data-current="${item.for_user ? "1" : "0"}">${forMeLabel}</button>
        <button class="link-btn detail-link" data-action="open-create-from-item" data-type="action" data-sig="${escapeHtml(sig)}">create ticket</button>
      `;
    }
    const removeBtn = (item.source === "manual" && manualIndex !== undefined && !item.done)
      ? `<button class="link-btn detail-link" data-action="remove-action" data-idx="${manualIndex}" title="Delete this manual action permanently">delete</button>`
      : "";
    return `
      <div class="${cls.join(" ")}">
        <div class="title">
          ${item.source === "manual" ? '<span class="source-badge">manual</span>' : ""}
          ${item.for_user ? '<span class="for-you">for you</span>' : ""}
          <span class="urgency urgency-${item.urgency}">${item.urgency}</span>
          ${escapeHtml(item.title)}
          ${actionsHtml}
          ${removeBtn}
        </div>
        <div class="detail">${escapeHtml(item.detail)}</div>
        ${item.ticket_keys.length ? `<div class="refs">→ ${item.ticket_keys.map(ticketKey).join(", ")}</div>` : ""}
        ${closureOpen ? closureForm("action", sig, "mark this action done") : ""}
        ${createOpen ? createFromItemForm("action", sig, manualIndex, {
          summary: item.title,
          description: item.detail,
          project: "KAHOOT",
        }) : ""}
      </div>
    `;
  };

  const riskItem = (r) => {
    const cls = ["analysis-item", `urgency-${r.severity}`];
    if (r.dismissed) cls.push("dismissed");
    const sig = r.sig || "";
    const closureOpen = sig && statusState.openClosure?.type === "risk" && statusState.openClosure?.sig === sig;
    const btn = r.dismissed
      ? `<button class="link-btn detail-link" data-action="restore-risk" data-sig="${escapeHtml(sig)}">restore</button>`
      : `<button class="link-btn detail-link" data-action="open-close" data-type="risk" data-sig="${escapeHtml(sig)}">dismiss</button>`;
    return `
      <div class="${cls.join(" ")}">
        <div class="title">
          <span class="urgency urgency-${r.severity}">${r.severity}</span>
          ${escapeHtml(r.title)}
          ${btn}
        </div>
        <div class="detail">${escapeHtml(r.detail)}</div>
        ${r.ticket_keys.length ? `<div class="refs">→ ${r.ticket_keys.map(ticketKey).join(", ")}</div>` : ""}
        ${closureOpen ? closureForm("risk", sig, "dismiss this risk") : ""}
      </div>
    `;
  };

  const gapItem = (g) => {
    const cls = ["analysis-item"];
    if (g.dismissed) cls.push("dismissed");
    const sig = g.sig || "";
    const closureOpen = sig && statusState.openClosure?.type === "gap" && statusState.openClosure?.sig === sig;
    const createOpen = sig && statusState.openCreateFromItem?.type === "gap" && statusState.openCreateFromItem?.sig === sig;
    const btnRow = g.dismissed
      ? `<button class="link-btn detail-link" data-action="restore-gap" data-sig="${escapeHtml(sig)}">restore</button>`
      : `
        <button class="link-btn detail-link" data-action="open-close" data-type="gap" data-sig="${escapeHtml(sig)}">dismiss</button>
        <button class="link-btn detail-link" data-action="open-create-from-item" data-type="gap" data-sig="${escapeHtml(sig)}">create ticket</button>
      `;
    return `
      <div class="${cls.join(" ")}">
        <div class="title">
          ${escapeHtml(g.title)}
          ${btnRow}
        </div>
        <div class="detail">${escapeHtml(g.detail)}</div>
        ${g.suggested_summary ? `<div class="refs">suggested ticket [${escapeHtml(g.suggested_project || "?")}]: ${escapeHtml(g.suggested_summary)}</div>` : ""}
        ${closureOpen ? closureForm("gap", sig, "dismiss this gap") : ""}
        ${createOpen ? createFromItemForm("gap", sig, undefined, {
          summary: g.suggested_summary || g.title,
          description: g.detail,
          project: g.suggested_project || "KAHOOT",
        }) : ""}
      </div>
    `;
  };

  // Inline form: capture "why are you closing this?" - stored to closure log.
  function closureForm(type, sig, verbLabel) {
    return `
      <div class="closure-form">
        <textarea
          data-action="closure-reason-input"
          rows="2"
          placeholder="Why ${verbLabel}? (e.g. 'normal for our team', 'already covered by FE-X', 'not relevant'). Optional but useful - the prompt-refinement skill reads this."
        ></textarea>
        <div class="form-actions">
          <button data-action="confirm-close" data-type="${type}" data-sig="${escapeHtml(sig)}">Confirm</button>
          <button class="secondary" data-action="cancel-close">Cancel</button>
        </div>
      </div>
    `;
  }

  function createFromItemForm(type, sig, manualIdx, prefill) {
    const idxAttr = manualIdx !== undefined ? `data-source-index="${manualIdx}"` : "";
    return `
      <div class="closure-form create-form">
        <div class="form-grid">
          <select data-action="ctf-project">
            <option value="KAHOOT" ${prefill.project === "KAHOOT" ? "selected" : ""}>KAHOOT (frontend)</option>
            <option value="BACK" ${prefill.project === "BACK" ? "selected" : ""}>BACK (backend)</option>
          </select>
          <select data-action="ctf-issuetype">
            <option value="Story" selected>Story</option>
            <option value="Task">Task</option>
            <option value="Bug">Bug</option>
          </select>
          <input type="text" data-action="ctf-summary" placeholder="Summary" value="${escapeHtml(prefill.summary || "")}" />
          <textarea data-action="ctf-description" rows="3" placeholder="Description (markdown ok)">${escapeHtml(prefill.description || "")}</textarea>
        </div>
        <div class="form-actions">
          <button data-action="confirm-create-from-item" data-type="${type}" data-sig="${escapeHtml(sig)}" ${idxAttr}>Create + close item</button>
          <button class="secondary" data-action="cancel-create-from-item">Cancel</button>
          <span class="status form-hint">Will create under ${escapeHtml(statusState.currentKey || "this epic")}, auto-transitioned to Selected for Development.</span>
        </div>
      </div>
    `;
  }

  const recItem = (r) => {
    const cls = ["analysis-item"];
    if (r.dismissed) cls.push("dismissed");
    const sig = r.sig || "";
    const closureOpen = sig && statusState.openClosure?.type === "recommendation" && statusState.openClosure?.sig === sig;
    const createOpen = sig && statusState.openCreateFromItem?.type === "recommendation" && statusState.openCreateFromItem?.sig === sig;
    const btnRow = r.dismissed
      ? `<button class="link-btn detail-link" data-action="restore-recommendation" data-sig="${escapeHtml(sig)}">restore</button>`
      : `
        <button class="link-btn detail-link" data-action="open-close" data-type="recommendation" data-sig="${escapeHtml(sig)}">dismiss</button>
        <button class="link-btn detail-link" data-action="open-create-from-item" data-type="recommendation" data-sig="${escapeHtml(sig)}">create ticket</button>
      `;
    return `
      <div class="${cls.join(" ")}">
        <div class="title">
          ${escapeHtml(r.title)}
          ${btnRow}
        </div>
        <div class="detail">${escapeHtml(r.detail)}</div>
        ${r.ticket_keys.length ? `<div class="refs">→ ${r.ticket_keys.map(ticketKey).join(", ")}</div>` : ""}
        ${closureOpen ? closureForm("recommendation", sig, "dismiss this recommendation") : ""}
        ${createOpen ? createFromItemForm("recommendation", sig, undefined, {
          summary: r.title,
          description: r.detail,
          project: "KAHOOT",
        }) : ""}
      </div>
    `;
  };

  // Build a flat index of manual actions so the remove button knows its position
  // in the server-side `actions_added` array. Manual items always come first in
  // the merged list (server-side ordering), so we can count them up to each item.
  let manualSeen = 0;
  const decoratedActions = (a?.action_items || []).map((item) => {
    if (item.source === "manual") {
      const wrapped = { item, manualIdx: manualSeen };
      manualSeen += 1;
      return wrapped;
    }
    return { item, manualIdx: undefined };
  });

  // Sort: open first, then manual, then for_user, then urgency. Done items
  // sink to the bottom (and are hidden behind a toggle by default).
  decoratedActions.sort((x, y) => {
    if (x.item.done !== y.item.done) return x.item.done ? 1 : -1;
    if ((x.item.source === "manual") !== (y.item.source === "manual"))
      return x.item.source === "manual" ? -1 : 1;
    if (x.item.for_user !== y.item.for_user)
      return x.item.for_user ? -1 : 1;
    const order = { high: 0, medium: 1, low: 2 };
    return order[x.item.urgency] - order[y.item.urgency];
  });
  const doneCount = decoratedActions.filter((d) => d.item.done).length;
  const visibleDecorated = statusState.showDoneActions
    ? decoratedActions
    : decoratedActions.filter((d) => !d.item.done);

  const visibleRisks = (a?.risks || []).filter((r) => !r.dismissed);
  const dismissedRisks = (a?.risks || []).filter((r) => r.dismissed);
  const showDismissed = statusState.showDismissedRisks === true;

  $("status-detail").innerHTML = `
    <div class="detail-header">
      <div>
        <h2>${escapeHtml(d.epic.key)} ${jiraLinkIcon(d.epic.key, d.epic.summary)} - ${escapeHtml(d.epic.summary)}</h2>
        <div class="meta">
          ${d.epic.status ? `status: ${escapeHtml(d.epic.status)} · ` : ""}
          ${d.epic.duedate ? `due: ${escapeHtml(d.epic.duedate)} · ` : ""}
          ${a ? `<span class="assessment assessment-${a.progress_assessment}">${escapeHtml(a.progress_assessment)}</span>` : `<span class="assessment assessment-loading">analyzing…</span>`}
        </div>
        ${(d.metadata?.stakeholder || d.metadata?.one_pager_url || (d.metadata?.segments || []).length) ? `
          <div class="meta epic-meta-row">
            ${d.metadata.stakeholder ? `<span class="idea-stakeholder">${escapeHtml(d.metadata.stakeholder)}</span>` : ""}
            ${(d.metadata.segments || []).map((s) => `<span class="segment-chip segment-${escapeHtml(s)}">${escapeHtml(s)}</span>`).join("")}
            ${d.metadata.one_pager_url ? `<a class="idea-onepager" href="${escapeHtml(d.metadata.one_pager_url)}" target="_blank" rel="noopener noreferrer">one-pager ↗</a>` : ""}
          </div>` : ""}
      </div>
      <div class="detail-actions">
        ${isJiraKey(d.epic.key) ? `<button class="secondary" data-action="create-stub" data-key="${escapeHtml(d.epic.key)}">+ Create ticket</button>` : ""}
        <button class="secondary" data-action="edit-metadata" data-key="${escapeHtml(d.epic.key)}">Edit metadata</button>
        <button class="secondary" data-action="refresh-detail" data-key="${escapeHtml(d.epic.key)}">Refresh analysis</button>
      </div>
    </div>

    <div id="metadata-edit-form" class="inline-form hidden">
      <div class="form-grid">
        <label>
          <span class="modal-field-label">Stakeholder</span>
          <input type="text" id="metadata-stakeholder" value="${escapeHtml(d.metadata?.stakeholder || "")}" placeholder="Who raised / owns this" />
        </label>
        <label>
          <span class="modal-field-label">Segments (audiences that benefit)</span>
          <div id="metadata-segments" class="segment-row">
            ${["business", "school", "home", "students", "internal"].map((s) => `
              <label class="segment-toggle segment-${s}">
                <input type="checkbox" data-action="metadata-segment" value="${s}" ${(d.metadata?.segments || []).includes(s) ? "checked" : ""} />
                <span>${s}</span>
              </label>
            `).join("")}
          </div>
        </label>
        <label>
          <span class="modal-field-label">Documents</span>
          <div id="metadata-docs" class="doc-list"></div>
          <button type="button" class="secondary doc-add-btn" data-action="metadata-add-doc">+ Add document</button>
        </label>
        <div class="form-actions">
          <button data-action="save-metadata" data-key="${escapeHtml(d.epic.key)}">Save</button>
          <button class="secondary" data-action="cancel-metadata">Cancel</button>
          <span id="metadata-form-status" class="status"></span>
        </div>
      </div>
    </div>

    <!-- ACTIONS BAR - first thing in detail, the most important section -->
    <section class="actions-bar">
      <div class="actions-bar-head">
        <h3>
          Action items
          <span class="count">(${decoratedActions.filter((d) => !d.item.done).length}${doneCount ? ` + ${doneCount} done` : ""})</span>
          ${doneCount ? `<button class="link-btn detail-link" data-action="toggle-done-actions">${statusState.showDoneActions ? "hide" : "show"} done</button>` : ""}
        </h3>
        <div class="actions-bar-buttons">
          <button class="secondary" data-action="toggle-add-action">+ Add action</button>
          <button class="secondary" data-action="toggle-extract">Extract from discussion</button>
        </div>
      </div>

      <!-- Inline form: add manual action (hidden by default) -->
      <div id="add-action-form" class="inline-form hidden">
        <div class="form-grid">
          <input type="text" id="new-action-title" placeholder="Action title (imperative: 'Confirm API schema with the data team')" />
          <textarea id="new-action-detail" rows="2" placeholder="1-2 sentences of context (optional)"></textarea>
          <select id="new-action-urgency">
            <option value="high">high</option>
            <option value="medium" selected>medium</option>
            <option value="low">low</option>
          </select>
          <label class="checkbox-inline">
            <input type="checkbox" id="new-action-for-user" />
            for me
          </label>
          <div class="form-actions">
            <button data-action="save-action" data-key="${escapeHtml(d.epic.key)}">Save</button>
            <button class="secondary" data-action="cancel-add-action">Cancel</button>
          </div>
        </div>
      </div>

      <!-- Inline form: extract actions from a discussion -->
      <div id="extract-form" class="inline-form hidden">
        <textarea id="extract-discussion" rows="6" placeholder="Paste meeting notes, Slack thread, planning conversation..."></textarea>
        <div class="form-actions">
          <button data-action="run-extract" data-key="${escapeHtml(d.epic.key)}">Extract actions</button>
          <button class="secondary" data-action="cancel-extract">Cancel</button>
          <span id="extract-status" class="status"></span>
        </div>
        <div id="extract-preview"></div>
      </div>

      ${analysisLoading ? '<p class="status loading"><span class="spinner"></span> analyzing actions (typically 30-60s)…</p>' : ""}
      ${!analysisLoading && a.action_items.length === 0 ? '<p class="empty-state">No action items yet. Add one manually or extract them from a discussion.</p>' : ""}
      ${visibleDecorated.map(({ item, manualIdx }) => actionItem(item, manualIdx)).join("")}
      ${!analysisLoading && a.action_items.length > 0 && visibleDecorated.length === 0 ? '<p class="empty-state">All actions done. Click "show done" to see them.</p>' : ""}
    </section>

    ${progressBar(d.counts)}
    ${d.role_split ? `
      <div class="analysis-section">
        <h4>Progress by role</h4>
        ${roleSplitRow(d.role_split, { compact: false })}
      </div>` : ""}

    ${analysisLoading ? loadingBlock("State of play") : `
    <div class="analysis-section">
      <h4>State of play</h4>
      <p>${escapeHtml(a.state_of_play)}</p>
    </div>`}

    ${analysisLoading ? loadingBlock("Risks") : ""}
    ${!analysisLoading && (visibleRisks.length || dismissedRisks.length) ? `
      <div class="analysis-section">
        <h4>
          Risks
          <span class="count">(${visibleRisks.length}${dismissedRisks.length ? ` + ${dismissedRisks.length} dismissed` : ""})</span>
          ${dismissedRisks.length ? `<button class="link-btn detail-link" data-action="toggle-dismissed-risks">${showDismissed ? "hide" : "show"} dismissed</button>` : ""}
        </h4>
        ${visibleRisks.map(riskItem).join("")}
        ${showDismissed ? dismissedRisks.map(riskItem).join("") : ""}
      </div>` : ""}

    ${analysisLoading ? loadingBlock("Gaps / missing scope") : ""}
    ${!analysisLoading && a.gaps.length ? (() => {
      const visibleGaps = a.gaps.filter((g) => !g.dismissed);
      const dismissedGaps = a.gaps.filter((g) => g.dismissed);
      const showDg = statusState.showDismissedGaps === true;
      return `
        <div class="analysis-section">
          <h4>
            Gaps / missing scope
            <span class="count">(${visibleGaps.length}${dismissedGaps.length ? ` + ${dismissedGaps.length} dismissed` : ""})</span>
            ${dismissedGaps.length ? `<button class="link-btn detail-link" data-action="toggle-dismissed-gaps">${showDg ? "hide" : "show"} dismissed</button>` : ""}
          </h4>
          ${visibleGaps.map(gapItem).join("")}
          ${showDg ? dismissedGaps.map(gapItem).join("") : ""}
        </div>`;
    })() : ""}

    ${analysisLoading ? loadingBlock("Recommendations") : ""}
    ${!analysisLoading && a.recommendations.length ? (() => {
      const visibleRecs = a.recommendations.filter((r) => !r.dismissed);
      const dismissedRecs = a.recommendations.filter((r) => r.dismissed);
      const showDr = statusState.showDismissedRecommendations === true;
      return `
        <div class="analysis-section">
          <h4>
            Recommendations
            <span class="count">(${visibleRecs.length}${dismissedRecs.length ? ` + ${dismissedRecs.length} dismissed` : ""})</span>
            ${dismissedRecs.length ? `<button class="link-btn detail-link" data-action="toggle-dismissed-recommendations">${showDr ? "hide" : "show"} dismissed</button>` : ""}
          </h4>
          ${visibleRecs.map(recItem).join("")}
          ${showDr ? dismissedRecs.map(recItem).join("") : ""}
        </div>`;
    })() : ""}

    <div class="analysis-section">
      <h4>Tickets (${d.tickets.length})</h4>
      ${d.tickets.length === 0 ? "<p class='sub'>(no child tickets)</p>" : `
        <div class="ticket-table">
          <div class="ticket-row ticket-header">
            ${sortHeader("key", "Key")}
            <div>Summary</div>
            ${sortHeader("status", "Status")}
            <div>Due</div>
            ${sortHeader("assignee", "Owner")}
          </div>
          ${sortTickets(d.tickets).map((t) => `
            <div class="ticket-row ${ticketRowClass(t)}">
              <div class="key">${escapeHtml(t.key)}${jiraLinkIcon(t.key, t.summary)}</div>
              <div>${escapeHtml(t.summary)}</div>
              <div class="status-cell"><span class="status-pill-${t.status_category}">${escapeHtml(t.status)}</span></div>
              <div class="duedate-cell">${t.duedate ? `due ${escapeHtml(t.duedate)}` : ""}</div>
              <div>${t.assignee ? escapeHtml(t.assignee.split(" ")[0]) : "-"}</div>
            </div>
          `).join("")}
        </div>
      `}
    </div>

    ${d.analyzed_at ? `<div class="analyzed-at">analyzed ${escapeHtml(d.analyzed_at)}</div>` : ""}
  `;
}

$("track-btn").addEventListener("click", trackEpic);
$("track-key").addEventListener("keydown", (e) => { if (e.key === "Enter") trackEpic(); });
$("refresh-all-btn").addEventListener("click", () => loadStatusList(true));

// Banner lives outside #tracked-list, so attach to the page-status main.
$("page-status").addEventListener("click", (e) => {
  const action = e.target.dataset.action;
  if (action === "apply-pending-refresh") applyPendingRefresh();
  else if (action === "dismiss-pending-refresh") dismissPendingRefresh();
  else if (action === "apply-pending-detail") applyPendingDetail();
  else if (action === "dismiss-pending-detail") dismissPendingDetail();
});

// Refresh the relative-time labels every 30s while the dashboard is visible
setInterval(() => {
  if (!$("page-status").classList.contains("hidden")) {
    renderDashboardLastSynced();
    if (statusState.entries.length) renderStatusList();
  }
}, 30000);

// Delegated handlers for cards / detail buttons
$("tracked-list").addEventListener("click", (e) => {
  // Anonymous users can read the dashboard list but not drill into details
  // (that endpoint is gated and would trigger an LLM call).
  if (!statusState.authenticated) return;

  const action = e.target.dataset.action;
  const key = e.target.dataset.key || e.target.closest("[data-key]")?.dataset.key;
  if (action === "remove" && key) {
    e.stopPropagation();
    removeTracked(key);
  } else if (action === "refresh-row" && key) {
    e.stopPropagation();
    refreshOneRow(key);
  } else if (action === "create-stub" && key) {
    e.stopPropagation();
    createStubTicket(key, e.target);
  } else if (key) {
    // Click on the active card collapses the detail; clicking another card
    // switches to that detail.
    if (statusState.currentKey === key && !$("status-detail-section").classList.contains("hidden")) {
      collapseDetail();
    } else {
      viewDetail(key);
    }
  }
});

function collapseDetail() {
  statusState.currentKey = null;
  statusState.detail = null;
  $("status-detail-section").classList.add("hidden");
  $("status-detail").innerHTML = "";
  renderStatusList();  // remove .active highlight
}
$("status-detail").addEventListener("input", (e) => {
  // Capture edits to metadata doc rows (rendered inside the dynamic detail HTML).
  const t = e.target;
  const idx = t.dataset?.docIdx;
  const field = t.dataset?.docField;
  if (idx === undefined || !field) return;
  if (!t.closest("#metadata-docs")) return;
  if (!statusState.editingMetaDocs?.[+idx]) return;
  statusState.editingMetaDocs[+idx][field] = t.value;
});

$("status-detail").addEventListener("click", (e) => {
  const target = e.target.closest("[data-action]");
  if (!target) return;
  const action = target.dataset.action;
  const key = statusState.currentKey;

  if (action === "refresh-detail") {
    viewDetail(target.dataset.key, true);
  } else if (action === "create-stub") {
    createStubTicket(target.dataset.key, target);
  } else if (action === "edit-metadata") {
    const form = $("metadata-edit-form");
    const opening = form.classList.contains("hidden");
    form.classList.toggle("hidden");
    if (opening) {
      // Snapshot current docs into the editor state
      statusState.editingMetaDocs = (statusState.detail?.metadata?.documents || []).map((d) => ({ ...d }));
      renderEditingMetaDocs();
    }
  } else if (action === "cancel-metadata") {
    $("metadata-edit-form").classList.add("hidden");
    statusState.editingMetaDocs = null;
  } else if (action === "save-metadata") {
    saveEpicMetadata(target.dataset.key);
  } else if (action === "metadata-add-doc") {
    addMetaDoc();
  } else if (target.dataset.docAction === "remove" && target.closest("#metadata-docs")) {
    // separate path so removing a metadata doc doesn't collide with other actions
    const idx = +target.dataset.docIdx;
    if (statusState.editingMetaDocs) {
      statusState.editingMetaDocs.splice(idx, 1);
      renderEditingMetaDocs();
    }
  } else if (action === "toggle-add-action") {
    const f = $("add-action-form");
    f.classList.toggle("hidden");
    $("extract-form").classList.add("hidden");
    if (!f.classList.contains("hidden")) $("new-action-title")?.focus();
  } else if (action === "cancel-add-action") {
    $("add-action-form").classList.add("hidden");
  } else if (action === "save-action") {
    saveManualAction(target.dataset.key);
  } else if (action === "remove-action") {
    removeManualAction(key, +target.dataset.idx);
  } else if (action === "reopen-action") {
    setActionDone(key, target.dataset.sig, false);
  } else if (action === "toggle-for-user") {
    toggleActionForUser(key, target.dataset.sig, target.dataset.current !== "1");
  } else if (action === "toggle-done-actions") {
    statusState.showDoneActions = !statusState.showDoneActions;
    renderDetail();
  } else if (action === "restore-risk") {
    restoreRisk(key, target.dataset.sig);
  } else if (action === "restore-gap") {
    restoreGap(key, target.dataset.sig);
  } else if (action === "restore-recommendation") {
    restoreRecommendation(key, target.dataset.sig);
  } else if (action === "toggle-dismissed-recommendations") {
    statusState.showDismissedRecommendations = !statusState.showDismissedRecommendations;
    renderDetail();
  } else if (action === "toggle-dismissed-risks") {
    statusState.showDismissedRisks = !statusState.showDismissedRisks;
    renderDetail();
  } else if (action === "toggle-dismissed-gaps") {
    statusState.showDismissedGaps = !statusState.showDismissedGaps;
    renderDetail();
  } else if (action === "open-close") {
    statusState.openClosure = { type: target.dataset.type, sig: target.dataset.sig };
    statusState.openCreateFromItem = null;
    renderDetail();
  } else if (action === "cancel-close") {
    statusState.openClosure = null;
    renderDetail();
  } else if (action === "confirm-close") {
    confirmClose(key, target.dataset.type, target.dataset.sig);
  } else if (action === "open-create-from-item") {
    statusState.openCreateFromItem = { type: target.dataset.type, sig: target.dataset.sig };
    statusState.openClosure = null;
    renderDetail();
  } else if (action === "cancel-create-from-item") {
    statusState.openCreateFromItem = null;
    renderDetail();
  } else if (action === "confirm-create-from-item") {
    confirmCreateFromItem(key, target.dataset.type, target.dataset.sig, target.dataset.sourceIndex);
  } else if (action === "sort-tickets") {
    const col = target.dataset.col;
    if (statusState.ticketsSortBy === col) {
      statusState.ticketsSortDir = statusState.ticketsSortDir === "asc" ? "desc" : "asc";
    } else {
      statusState.ticketsSortBy = col;
      statusState.ticketsSortDir = "asc";
    }
    renderDetail();
  } else if (action === "toggle-extract") {
    const f = $("extract-form");
    f.classList.toggle("hidden");
    $("add-action-form").classList.add("hidden");
    if (!f.classList.contains("hidden")) $("extract-discussion")?.focus();
  } else if (action === "cancel-extract") {
    $("extract-form").classList.add("hidden");
    statusState.extractedActions = null;
  } else if (action === "run-extract") {
    runExtractActions(target.dataset.key);
  } else if (action === "save-extracted") {
    saveExtractedActions(target.dataset.key);
  } else if (action === "toggle-extracted") {
    const idx = +target.dataset.idx;
    statusState.extractedActions.proposed[idx]._skip = !statusState.extractedActions.proposed[idx]._skip;
    renderExtractedPreview();
  }
});

async function saveManualAction(key) {
  const title = $("new-action-title").value.trim();
  if (!title) {
    alert("Title is required");
    return;
  }
  const body = {
    title,
    detail: $("new-action-detail").value.trim(),
    urgency: $("new-action-urgency").value,
    for_user: $("new-action-for-user").checked,
  };
  try {
    const resp = await fetch(`/api/tracked/${encodeURIComponent(key)}/actions`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({ detail: resp.statusText }));
      throw new Error(err.detail || resp.statusText);
    }
    await refreshDetailFromCache(key);
  } catch (e) {
    alert(`Save failed: ${e.message}`);
  }
}

async function removeManualAction(key, index) {
  if (!confirm("Remove this action?")) return;
  try {
    const resp = await fetch(`/api/tracked/${encodeURIComponent(key)}/actions/${index}`, { method: "DELETE" });
    if (!resp.ok) throw new Error(resp.statusText);
    await refreshDetailFromCache(key);
  } catch (e) {
    alert(`Remove failed: ${e.message}`);
  }
}

async function confirmClose(key, type, sig) {
  if (!sig || !type) return;
  const textarea = document.querySelector('[data-action="closure-reason-input"]');
  const reason = textarea ? textarea.value.trim() : "";
  const url = closeUrlFor(key, type, sig);
  if (!url) return;
  try {
    const resp = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ reason: reason || null }),
    });
    if (!resp.ok) throw new Error(resp.statusText);
    // Patch local state so the UI updates without a server round-trip.
    if (type === "risk") {
      const r = statusState.detail.analysis.risks.find((x) => x.sig === sig);
      if (r) r.dismissed = true;
    } else if (type === "gap") {
      const g = statusState.detail.analysis.gaps.find((x) => x.sig === sig);
      if (g) g.dismissed = true;
    } else if (type === "recommendation") {
      const r = statusState.detail.analysis.recommendations.find((x) => x.sig === sig);
      if (r) r.dismissed = true;
    } else if (type === "action") {
      const a = statusState.detail.analysis.action_items.find((x) => x.sig === sig);
      if (a) a.done = true;
    }
    statusState.openClosure = null;
    renderDetail();
    // Refresh dashboard chips when an action is closed - risks/gaps don't
    // affect dashboard counts, only actions do.
    if (type === "action") refreshOneRow(key).catch(() => {});
  } catch (e) {
    alert(`Close failed: ${e.message}`);
  }
}

function closeUrlFor(key, type, sig) {
  const k = encodeURIComponent(key);
  const s = encodeURIComponent(sig);
  if (type === "risk") return `/api/tracked/${k}/risks/${s}/dismiss`;
  if (type === "gap") return `/api/tracked/${k}/gaps/${s}/dismiss`;
  if (type === "recommendation") return `/api/tracked/${k}/recommendations/${s}/dismiss`;
  if (type === "action") return `/api/tracked/${k}/actions/${s}/done`;
  return null;
}

async function restoreRisk(key, sig) {
  if (!sig) return;
  try {
    const resp = await fetch(`/api/tracked/${encodeURIComponent(key)}/risks/${encodeURIComponent(sig)}/dismiss`, { method: "DELETE" });
    if (!resp.ok) throw new Error(resp.statusText);
    const r = statusState.detail.analysis.risks.find((x) => x.sig === sig);
    if (r) r.dismissed = false;
    renderDetail();
  } catch (e) {
    alert(`Restore failed: ${e.message}`);
  }
}

async function restoreGap(key, sig) {
  if (!sig) return;
  try {
    const resp = await fetch(`/api/tracked/${encodeURIComponent(key)}/gaps/${encodeURIComponent(sig)}/dismiss`, { method: "DELETE" });
    if (!resp.ok) throw new Error(resp.statusText);
    const g = statusState.detail.analysis.gaps.find((x) => x.sig === sig);
    if (g) g.dismissed = false;
    renderDetail();
  } catch (e) {
    alert(`Restore failed: ${e.message}`);
  }
}

async function restoreRecommendation(key, sig) {
  if (!sig) return;
  try {
    const resp = await fetch(`/api/tracked/${encodeURIComponent(key)}/recommendations/${encodeURIComponent(sig)}/dismiss`, { method: "DELETE" });
    if (!resp.ok) throw new Error(resp.statusText);
    const r = statusState.detail.analysis.recommendations.find((x) => x.sig === sig);
    if (r) r.dismissed = false;
    renderDetail();
  } catch (e) {
    alert(`Restore failed: ${e.message}`);
  }
}

async function confirmCreateFromItem(key, type, sig, sourceIndex) {
  const container = document.querySelector('[data-action="confirm-create-from-item"]')?.closest(".create-form");
  if (!container) return;
  const summary = container.querySelector('[data-action="ctf-summary"]')?.value.trim();
  const description = container.querySelector('[data-action="ctf-description"]')?.value.trim() || "";
  const project = container.querySelector('[data-action="ctf-project"]')?.value || "KAHOOT";
  const issuetype = container.querySelector('[data-action="ctf-issuetype"]')?.value || "Story";
  if (!summary) {
    alert("Summary is required");
    return;
  }
  try {
    const resp = await fetch(`/api/tracked/${encodeURIComponent(key)}/create-from-item`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        project,
        summary,
        description,
        issuetype,
        source_type: type,
        source_sig: sig,
        source_index: sourceIndex !== undefined ? +sourceIndex : null,
      }),
    });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({ detail: resp.statusText }));
      throw new Error(err.detail || resp.statusText);
    }
    const result = await resp.json();
    statusState.openCreateFromItem = null;
    // Re-fetch the detail so it picks up the dismissed/done state set server-side
    // (and so manual actions that got removed are gone).
    await refreshDetailFromCache(key);
    // Tiny success ping
    const msg = `created ${result.key}${result.transitioned_to ? ` (${result.transitioned_to})` : ""}`;
    const banner = document.createElement("div");
    banner.className = "create-banner";
    banner.textContent = msg;
    $("status-detail").prepend(banner);
    setTimeout(() => banner.remove(), 4000);
  } catch (e) {
    alert(`Create failed: ${e.message}`);
  }
}

async function toggleActionForUser(key, sig, newValue) {
  if (!sig) return;
  try {
    const resp = await fetch(
      `/api/tracked/${encodeURIComponent(key)}/actions/${encodeURIComponent(sig)}/for-user`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ for_user: newValue }),
      },
    );
    if (!resp.ok) throw new Error(resp.statusText);
    // Patch local detail view
    if (statusState.detail) {
      const item = statusState.detail.analysis.action_items.find((x) => x.sig === sig);
      if (item) item.for_user = newValue;
    }
    // Patch local Actions tab if it's loaded
    for (const g of actionsState.groups) {
      const item = g.actions.find((x) => x.sig === sig);
      if (item) item.for_user = newValue;
    }
    renderDetail();
    if (!$("page-actions").classList.contains("hidden")) renderActionsTab();
    // Keep the dashboard chip count in sync.
    refreshOneRow(key).catch(() => {});
  } catch (e) {
    alert(`Toggle failed: ${e.message}`);
  }
}

async function setActionDone(key, sig, done) {
  if (!sig) return;
  const url = `/api/tracked/${encodeURIComponent(key)}/actions/${encodeURIComponent(sig)}/done`;
  try {
    const resp = await fetch(url, { method: done ? "POST" : "DELETE" });
    if (!resp.ok) throw new Error(resp.statusText);
    // Toggle locally so the UI updates instantly without refetching the detail.
    const items = statusState.detail.analysis.action_items;
    const item = items.find((x) => x.sig === sig);
    if (item) item.done = done;
    renderDetail();
    // Refresh dashboard summary chips so "N actions" / "N for you" stay accurate.
    refreshOneRow(key).catch(() => {});
  } catch (e) {
    alert(`Update failed: ${e.message}`);
  }
}

function renderEditingMetaDocs() {
  const docs = statusState.editingMetaDocs || [];
  const html = docs.map((d, idx) => docRowHtml(d, idx)).join("");
  $("metadata-docs").innerHTML = html || `<div class="doc-empty">No documents yet. Add a Confluence page or any URL.</div>`;
}

function addMetaDoc() {
  statusState.editingMetaDocs = statusState.editingMetaDocs || [];
  statusState.editingMetaDocs.push({ url: "", label: "" });
  renderEditingMetaDocs();
  setTimeout(() => {
    const rows = $("metadata-docs").querySelectorAll(".doc-row");
    rows[rows.length - 1]?.querySelector('input[data-doc-field="url"]')?.focus();
  }, 50);
}

async function saveEpicMetadata(key) {
  const stakeholder = $("metadata-stakeholder").value.trim();
  const docsPayload = (statusState.editingMetaDocs || [])
    .filter((d) => (d.url || "").trim())
    .map((d) => ({ url: d.url.trim(), label: (d.label || "").trim() || null }));
  const segments = [...document.querySelectorAll('[data-action="metadata-segment"]:checked')]
    .map((el) => el.value);
  $("metadata-form-status").className = "status loading";
  $("metadata-form-status").innerHTML = `<span class="spinner"></span> saving + fetching docs…`;
  try {
    const resp = await fetch(`/api/tracked/${encodeURIComponent(key)}/metadata`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ stakeholder, documents: docsPayload, segments }),
    });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({ detail: resp.statusText }));
      throw new Error(err.detail || resp.statusText);
    }
    if (statusState.detail) {
      statusState.detail.metadata = await resp.json();
    }
    const entryIdx = statusState.entries.findIndex((e) => e.key === key);
    if (entryIdx >= 0 && statusState.detail?.metadata) {
      statusState.entries[entryIdx] = {
        ...statusState.entries[entryIdx],
        metadata: statusState.detail.metadata,
      };
    }
    statusState.editingMetaDocs = null;
    renderDetail();
    renderStatusList();
  } catch (e) {
    $("metadata-form-status").className = "status error";
    $("metadata-form-status").textContent = `error: ${e.message}`;
  }
}

async function refreshDetailFromCache(key) {
  // After mutating overrides, refetch the detail (which now reflects the change).
  // Uses GET (no force-refresh) so we don't re-run the LLM analysis.
  const resp = await fetch(`/api/tracked/${encodeURIComponent(key)}`);
  if (!resp.ok) return;
  statusState.detail = await resp.json();
  renderDetail();
}

async function runExtractActions(key) {
  const discussion = $("extract-discussion").value.trim();
  if (!discussion) {
    $("extract-status").className = "status error";
    $("extract-status").textContent = "paste a discussion first";
    return;
  }
  const stop = startStatusSpinner("extract-status", "asking Claude to extract actions…");
  try {
    const resp = await fetch(`/api/tracked/${encodeURIComponent(key)}/extract-actions`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ discussion }),
    });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({ detail: resp.statusText }));
      throw new Error(err.detail || resp.statusText);
    }
    statusState.extractedActions = await resp.json();
    // Mark all as approved by default
    statusState.extractedActions.proposed.forEach((a) => { a._skip = false; });
    renderExtractedPreview();
    stop(`${statusState.extractedActions.proposed.length} proposed - review and save`);
  } catch (e) {
    stop(`error: ${e.message}`, true);
  }
}

function renderExtractedPreview() {
  const container = $("extract-preview");
  if (!container) return;
  const ex = statusState.extractedActions;
  if (!ex || !ex.proposed.length) {
    container.innerHTML = ex ? '<p class="empty-state">Claude found no actionable items.</p>' : "";
    return;
  }
  container.innerHTML = `
    ${ex.notes ? `<div class="extract-note">${escapeHtml(ex.notes)}</div>` : ""}
    <div class="extracted-list">
      ${ex.proposed.map((a, i) => `
        <label class="extracted-item ${a._skip ? "skipped" : ""}">
          <input type="checkbox" ${a._skip ? "" : "checked"} data-action="toggle-extracted" data-idx="${i}" />
          <div>
            <div class="title">
              <span class="urgency urgency-${a.urgency}">${a.urgency}</span>
              ${escapeHtml(a.title)}
              ${a.for_user ? '<span class="for-you">for you</span>' : ""}
            </div>
            <div class="detail">${escapeHtml(a.detail)}</div>
            ${a.ticket_keys && a.ticket_keys.length ? `<div class="refs">→ ${a.ticket_keys.map(ticketKey).join(", ")}</div>` : ""}
          </div>
        </label>
      `).join("")}
    </div>
    <div class="form-actions">
      <button data-action="save-extracted" data-key="${escapeHtml(statusState.currentKey)}">Save approved (${ex.proposed.filter((a) => !a._skip).length})</button>
    </div>
  `;
}

async function saveExtractedActions(key) {
  const approved = (statusState.extractedActions?.proposed || []).filter((a) => !a._skip);
  if (!approved.length) {
    alert("Nothing approved to save");
    return;
  }
  try {
    // Send each as a manual action (server marks source=manual)
    for (const a of approved) {
      const resp = await fetch(`/api/tracked/${encodeURIComponent(key)}/actions`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          title: a.title,
          detail: a.detail,
          urgency: a.urgency,
          ticket_keys: a.ticket_keys || [],
          for_user: !!a.for_user,
        }),
      });
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({ detail: resp.statusText }));
        throw new Error(err.detail || resp.statusText);
      }
    }
    statusState.extractedActions = null;
    $("extract-form").classList.add("hidden");
    $("extract-discussion").value = "";
    await refreshDetailFromCache(key);
  } catch (e) {
    alert(`Save failed: ${e.message}`);
  }
}

// ---------- Slack reply page ----------

async function generateSlackReply() {
  const context = $("slack-context").value;
  const draft = $("slack-draft").value;
  const audience = document.querySelector('input[name="slack-audience"]:checked')?.value || "other";
  if (!context.trim()) {
    $("slack-status").textContent = "context is required";
    $("slack-status").className = "status error";
    return;
  }
  const stop = startLoading(
    "slack-generate-btn",
    "slack-status",
    `asking Claude (audience: ${audience})…`,
    "Generating…",
  );

  try {
    const resp = await fetch("/api/slack-reply", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ context, draft: draft || null, audience }),
    });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({ detail: resp.statusText }));
      throw new Error(err.detail || resp.statusText);
    }
    const data = await resp.json();
    $("slack-output-section").classList.remove("hidden");
    $("slack-output").textContent = data.message;
    $("slack-notes").textContent = data.notes || "";
    $("slack-notes").classList.toggle("hidden", !data.notes);
    $("slack-copy-status").textContent = "";
    stop("ready - review and copy");
  } catch (e) {
    stop(`error: ${e.message}`, true);
  }
}

async function copySlackReply() {
  const text = $("slack-output").textContent;
  if (!text) return;
  try {
    await navigator.clipboard.writeText(text);
    $("slack-copy-status").className = "status";
    $("slack-copy-status").textContent = "copied";
    setTimeout(() => { $("slack-copy-status").textContent = ""; }, 2000);
  } catch (e) {
    $("slack-copy-status").className = "status error";
    $("slack-copy-status").textContent = `copy failed: ${e.message}`;
  }
}

$("slack-generate-btn").addEventListener("click", generateSlackReply);
$("slack-copy-btn").addEventListener("click", copySlackReply);

// ---------- Team members (Settings) ----------

const teamState = { members: [] };
const ROLE_OPTIONS = ["backend", "frontend", "design", "other"];

function renderTeam() {
  const container = $("team-members-list");
  if (!teamState.members.length) {
    container.innerHTML = `<div class="empty-team">No team members yet. Click "Add member" to start.</div>`;
    return;
  }
  container.innerHTML = teamState.members
    .map((m, i) => `
      <div class="team-row">
        <input type="text" placeholder="Name (e.g. Jane Doe)" value="${escapeHtml(m.name || "")}" data-team-idx="${i}" data-team-field="name" />
        <input type="text" placeholder="Email (optional)" value="${escapeHtml(m.email || "")}" data-team-idx="${i}" data-team-field="email" />
        <select data-team-idx="${i}" data-team-field="role">
          ${ROLE_OPTIONS.map((r) => `<option value="${r}" ${m.role === r ? "selected" : ""}>${r}</option>`).join("")}
        </select>
        <button type="button" class="secondary" data-action="team-remove" data-idx="${i}">Remove</button>
      </div>
    `)
    .join("");
}

async function loadTeam() {
  try {
    const resp = await fetch("/api/team");
    if (!resp.ok) throw new Error(resp.statusText);
    const data = await resp.json();
    teamState.members = data.members || [];
    renderTeam();
  } catch (e) {
    $("team-status").className = "status error";
    $("team-status").textContent = `failed to load team: ${e.message}`;
  }
}

async function saveTeam() {
  // Drop entries with empty name (placeholders) before sending
  const payload = teamState.members.filter((m) => m.name && m.name.trim());
  $("team-status").className = "status";
  $("team-status").textContent = "saving…";
  try {
    const resp = await fetch("/api/team", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ members: payload }),
    });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({ detail: resp.statusText }));
      throw new Error(err.detail || resp.statusText);
    }
    const data = await resp.json();
    teamState.members = data.members || [];
    renderTeam();
    $("team-status").textContent = `saved (${teamState.members.length} member${teamState.members.length === 1 ? "" : "s"})`;
    setTimeout(() => { $("team-status").textContent = ""; }, 2500);
  } catch (e) {
    $("team-status").className = "status error";
    $("team-status").textContent = `error: ${e.message}`;
  }
}

$("team-add-btn").addEventListener("click", () => {
  teamState.members.push({ name: "", email: "", role: "backend" });
  renderTeam();
});

$("team-save-btn").addEventListener("click", saveTeam);

$("team-members-list").addEventListener("input", (e) => {
  const t = e.target;
  if (t.dataset.teamIdx === undefined) return;
  const idx = +t.dataset.teamIdx;
  const field = t.dataset.teamField;
  teamState.members[idx][field] = t.value;
});

$("team-members-list").addEventListener("change", (e) => {
  const t = e.target;
  if (t.tagName === "SELECT" && t.dataset.teamIdx !== undefined) {
    teamState.members[+t.dataset.teamIdx][t.dataset.teamField] = t.value;
  }
});

$("team-members-list").addEventListener("click", (e) => {
  if (e.target.dataset.action === "team-remove") {
    teamState.members.splice(+e.target.dataset.idx, 1);
    renderTeam();
  }
});

// Fetch Atlassian domain once on startup so jiraLinkIcon works on every page
// (Sync, Status, etc.) without each page having to remember to load it.
(async () => {
  try {
    const resp = await fetch("/api/settings");
    if (!resp.ok) return;
    const s = await resp.json();
    statusState.atlassianDomain = statusState.atlassianDomain || s.ATLASSIAN_DOMAIN || null;
    statusState.userEmail = statusState.userEmail || s.ATLASSIAN_EMAIL || null;
    if (!$("page-status").classList.contains("hidden") && statusState.entries.length) {
      renderStatusList();
    }
  } catch {}
})();

// ---------- Authentication ----------

function applyAuthState() {
  document.body.dataset.auth = statusState.authenticated ? "auth" : "anon";
  // If anon, force route to Projects Dashboard - the only page they can use.
  if (!statusState.authenticated && window.location.pathname !== "/projects") {
    history.replaceState({ page: "status" }, "", "/projects");
    showPage("status", { pushHistory: false });
  }
}

async function refreshAuthStatus() {
  try {
    const resp = await fetch("/api/auth/status");
    if (!resp.ok) return;
    const s = await resp.json();
    statusState.authenticated = !!s.authenticated;
    statusState.authMode = s.mode || "password";
    applyAuthState();
  } catch {}
}

async function performLogin() {
  const pw = $("login-password").value;
  if (!pw) {
    $("login-status").className = "status error";
    $("login-status").textContent = "password required";
    return;
  }
  $("login-status").className = "status";
  $("login-status").textContent = "checking…";
  try {
    const resp = await fetch("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password: pw }),
    });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({ detail: resp.statusText }));
      throw new Error(err.detail || resp.statusText);
    }
    statusState.authenticated = true;
    applyAuthState();
    $("login-modal").classList.add("hidden");
    $("login-password").value = "";
    $("login-status").textContent = "";
    // Reload dashboard so anything that needs auth (refresh, detail) is now usable.
    if (!$("page-status").classList.contains("hidden")) await loadStatusList();
  } catch (e) {
    $("login-status").className = "status error";
    $("login-status").textContent = `error: ${e.message}`;
  }
}

async function performLogout() {
  try {
    await fetch("/api/auth/logout", { method: "POST" });
  } catch {}
  statusState.authenticated = false;
  applyAuthState();
  // Collapse any open detail since anon can't keep it visible.
  collapseDetail();
}

$("auth-login-btn").addEventListener("click", () => {
  $("login-modal").classList.remove("hidden");
  setTimeout(() => $("login-password").focus(), 50);
});
$("auth-logout-btn").addEventListener("click", performLogout);
$("login-confirm-btn").addEventListener("click", performLogin);
$("login-cancel-btn").addEventListener("click", () => {
  $("login-modal").classList.add("hidden");
  $("login-password").value = "";
  $("login-status").textContent = "";
});
$("login-password").addEventListener("keydown", (e) => {
  if (e.key === "Enter") performLogin();
});

// ---------- Ideas (pre-project capture, kanban) ----------

const IDEA_COLUMNS = [
  { status: "exploring", label: "Exploring" },
  { status: "parked", label: "Parked" },
  { status: "queued", label: "Queued" },
  { status: "promoted", label: "Promoted" },
  { status: "dropped", label: "Dropped" },
];
const ideaState = { ideas: [], dragging: null };

async function loadIdeas() {
  try {
    const resp = await fetch("/api/ideas");
    if (!resp.ok) throw new Error(resp.statusText);
    const data = await resp.json();
    ideaState.ideas = data.ideas || [];
    renderIdeas();
  } catch (e) {
    $("idea-board").innerHTML = `<p class="status error">failed to load: ${escapeHtml(e.message)}</p>`;
  }
}

function renderIdeas() {
  const board = $("idea-board");
  const byStatus = Object.fromEntries(IDEA_COLUMNS.map((c) => [c.status, []]));
  for (const i of ideaState.ideas) {
    (byStatus[i.status] || byStatus.exploring).push(i);
  }
  for (const status in byStatus) {
    byStatus[status].sort((a, b) => a.position - b.position);
  }
  board.innerHTML = IDEA_COLUMNS.map((c) => `
    <div class="idea-column" data-status="${c.status}">
      <div class="idea-column-header">
        <span>${escapeHtml(c.label)}</span>
        <span class="idea-column-count">${byStatus[c.status].length}</span>
      </div>
      ${byStatus[c.status].map(ideaCard).join("")}
    </div>
  `).join("");

  // Wire drag-drop listeners (anonymous users skip - they can't drag anyway).
  if (statusState.authenticated) {
    board.querySelectorAll(".idea-card").forEach((el) => {
      el.setAttribute("draggable", "true");
      el.addEventListener("dragstart", onIdeaDragStart);
      el.addEventListener("dragend", onIdeaDragEnd);
    });
    board.querySelectorAll(".idea-column").forEach((col) => {
      col.addEventListener("dragover", onColumnDragOver);
      col.addEventListener("dragleave", onColumnDragLeave);
      col.addEventListener("drop", onColumnDrop);
    });
  }
}

function ideaCard(i) {
  const promoted = i.promoted_epic_key
    ? `<span class="promoted-link">${ticketKey(i.promoted_epic_key)}</span>`
    : "";
  const docs = i.documents || [];
  // First doc renders as the headline link; additional ones get a count badge.
  const firstDoc = docs[0];
  const moreDocs = docs.length > 1 ? `<span class="doc-count">+${docs.length - 1} more</span>` : "";
  const segs = i.segments || [];
  return `
    <div class="idea-card" data-id="${escapeHtml(i.id)}">
      <div class="idea-title">${escapeHtml(i.title)}</div>
      ${i.notes ? `<div class="idea-notes">${escapeHtml(i.notes)}</div>` : ""}
      <div class="idea-meta">
        ${i.stakeholder ? `<span class="idea-stakeholder">${escapeHtml(i.stakeholder)}</span>` : ""}
        ${firstDoc ? `<a class="idea-onepager" href="${escapeHtml(firstDoc.url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(firstDoc.label || (firstDoc.kind === "confluence" ? "confluence" : "link"))} ↗</a>` : ""}
        ${moreDocs}
        ${promoted}
      </div>
      ${segs.length ? `<div class="idea-segments">${segs.map((s) => `<span class="segment-chip segment-${escapeHtml(s)}">${escapeHtml(s)}</span>`).join("")}</div>` : ""}
      <div class="idea-actions">
        <button data-action="edit-idea" data-id="${escapeHtml(i.id)}">edit</button>
        ${i.status !== "promoted" ? `<button data-action="promote-idea" data-id="${escapeHtml(i.id)}">promote →</button>` : ""}
        <button data-action="delete-idea" data-id="${escapeHtml(i.id)}" class="danger-link">delete</button>
      </div>
    </div>
  `;
}

// ---- drag-drop ----

function onIdeaDragStart(e) {
  const id = e.currentTarget.dataset.id;
  ideaState.dragging = id;
  e.currentTarget.classList.add("dragging");
  e.dataTransfer.effectAllowed = "move";
  // Firefox needs setData to allow dragging
  e.dataTransfer.setData("text/plain", id);
}

function onIdeaDragEnd(e) {
  e.currentTarget.classList.remove("dragging");
  ideaState.dragging = null;
  document.querySelectorAll(".idea-column.drag-over").forEach((el) => el.classList.remove("drag-over"));
}

function onColumnDragOver(e) {
  e.preventDefault();
  e.dataTransfer.dropEffect = "move";
  e.currentTarget.classList.add("drag-over");
}

function onColumnDragLeave(e) {
  if (!e.currentTarget.contains(e.relatedTarget)) {
    e.currentTarget.classList.remove("drag-over");
  }
}

async function onColumnDrop(e) {
  e.preventDefault();
  const targetCol = e.currentTarget;
  targetCol.classList.remove("drag-over");
  const draggedId = ideaState.dragging || e.dataTransfer.getData("text/plain");
  if (!draggedId) return;
  const newStatus = targetCol.dataset.status;
  const before = ideaState.ideas.find((i) => i.id === draggedId);
  const wasPromotedAlready = before?.status === "promoted";

  // Figure out the drop index by checking which card the pointer is over
  const cards = [...targetCol.querySelectorAll(".idea-card:not(.dragging)")];
  let dropIndex = cards.length;
  for (let i = 0; i < cards.length; i += 1) {
    const rect = cards[i].getBoundingClientRect();
    if (e.clientY < rect.top + rect.height / 2) {
      dropIndex = i;
      break;
    }
  }

  // Build the new ordering for the destination column
  const order = [];
  let pos = 0;
  const insertSrcAt = (status, position) => order.push({ id: draggedId, status, position });
  cards.forEach((cardEl, idx) => {
    if (idx === dropIndex) insertSrcAt(newStatus, pos++);
    order.push({ id: cardEl.dataset.id, status: newStatus, position: pos++ });
  });
  if (dropIndex === cards.length) insertSrcAt(newStatus, pos);

  try {
    const resp = await fetch("/api/ideas/reorder", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ order }),
    });
    if (!resp.ok) throw new Error(resp.statusText);
    const data = await resp.json();
    ideaState.ideas = data.ideas || [];
    renderIdeas();
    // If the user just dropped into Promoted (and it wasn't there before),
    // jump straight to Manage requirements with prefilled context. Matches the
    // explicit "Promote →" button behavior.
    if (newStatus === "promoted" && !wasPromotedAlready) {
      const updated = ideaState.ideas.find((i) => i.id === draggedId);
      if (updated) jumpToManageRequirements(updated);
    }
  } catch (err) {
    alert(`Move failed: ${err.message}`);
    loadIdeas();
  }
}

// ---- add / edit / delete / promote ----

function openAddIdeaForm() {
  $("idea-add-form").classList.remove("hidden");
  renderSegmentToggles("new-idea-segments", []);
  $("new-idea-title").focus();
}
function closeAddIdeaForm() {
  $("idea-add-form").classList.add("hidden");
  ["new-idea-title", "new-idea-notes", "new-idea-onepager", "new-idea-stakeholder"].forEach((id) => { $(id).value = ""; });
  renderSegmentToggles("new-idea-segments", []);
  $("idea-form-status").textContent = "";
}

const SEGMENT_NAMES = ["business", "school", "home", "students", "internal"];

function renderSegmentToggles(hostId, current) {
  const host = $(hostId);
  if (!host) return;
  const selected = new Set(current || []);
  host.innerHTML = SEGMENT_NAMES.map((s) => `
    <label class="segment-toggle segment-${s}">
      <input type="checkbox" data-segment="${s}" ${selected.has(s) ? "checked" : ""} />
      <span>${s}</span>
    </label>
  `).join("");
}

function readSegmentToggles(hostId) {
  return [...$(hostId).querySelectorAll('input[type="checkbox"][data-segment]')]
    .filter((cb) => cb.checked)
    .map((cb) => cb.dataset.segment);
}

async function saveIdea() {
  const title = $("new-idea-title").value.trim();
  if (!title) {
    $("idea-form-status").className = "status error";
    $("idea-form-status").textContent = "title required";
    return;
  }
  try {
    const resp = await fetch("/api/ideas", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        title,
        notes: $("new-idea-notes").value,
        one_pager_url: $("new-idea-onepager").value || null,
        stakeholder: $("new-idea-stakeholder").value || null,
        segments: readSegmentToggles("new-idea-segments"),
      }),
    });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({ detail: resp.statusText }));
      throw new Error(err.detail || resp.statusText);
    }
    closeAddIdeaForm();
    await loadIdeas();
  } catch (e) {
    $("idea-form-status").className = "status error";
    $("idea-form-status").textContent = `error: ${e.message}`;
  }
}

function editIdea(id) {
  const idea = ideaState.ideas.find((i) => i.id === id);
  if (!idea) return;
  ideaState.editing = id;
  // Working copy of documents - mutated by add/remove until Save persists it
  ideaState.editingDocs = (idea.documents || []).map((d) => ({ ...d }));
  $("idea-edit-title").value = idea.title || "";
  $("idea-edit-notes").value = idea.notes || "";
  $("idea-edit-stakeholder").value = idea.stakeholder || "";
  $("idea-edit-status-select").value = idea.status || "exploring";
  renderSegmentToggles("idea-edit-segments", idea.segments || []);
  renderEditingDocs();
  // Meta strip - read-only context the user can see
  const created = idea.created_at ? new Date(idea.created_at).toLocaleString() : "?";
  const updated = idea.updated_at ? new Date(idea.updated_at).toLocaleString() : "?";
  const promoted = idea.promoted_epic_key
    ? `<br>promoted to ${ticketKey(idea.promoted_epic_key)}`
    : "";
  $("idea-edit-meta").innerHTML = `
    id: ${escapeHtml(idea.id)}<br>
    created: ${escapeHtml(created)}<br>
    updated: ${escapeHtml(updated)}${promoted}
  `;
  $("idea-edit-status").textContent = "";
  $("idea-edit-modal").classList.remove("hidden");
  setTimeout(() => $("idea-edit-title").focus(), 50);
}

function closeIdeaEdit() {
  $("idea-edit-modal").classList.add("hidden");
  $("idea-edit-status").textContent = "";
  ideaState.editing = null;
  ideaState.editingDocs = null;
}

function renderEditingDocs() {
  const docs = ideaState.editingDocs || [];
  const html = docs.map((d, idx) => docRowHtml(d, idx)).join("");
  $("idea-edit-docs").innerHTML = html || `<div class="doc-empty">No documents yet. Add a Confluence page or any URL.</div>`;
}

function docRowHtml(doc, idx) {
  let badge = "";
  const fetchable = doc.kind === "confluence" || doc.kind === "figma" || doc.kind === "google-drive";
  const kindLabel = doc.kind === "google-drive" ? "google" : doc.kind;
  if (doc.fetch_error) {
    badge = `<span class="doc-badge doc-badge-err" title="${escapeHtml(doc.fetch_error)}">⚠ ${escapeHtml(kindLabel)} fetch failed</span>`;
  } else if (fetchable && doc.cached_text) {
    badge = `<span class="doc-badge doc-badge-ok" title="Cached ${escapeHtml(doc.cached_at || "")}">✓ ${escapeHtml(kindLabel)}</span>`;
  } else if (fetchable) {
    badge = `<span class="doc-badge doc-badge-pending">↻ ${escapeHtml(kindLabel)} - will fetch on save</span>`;
  } else {
    badge = `<span class="doc-badge">link</span>`;
  }
  const openLink = doc.url
    ? `<a class="doc-open" href="${escapeHtml(doc.url)}" target="_blank" rel="noopener noreferrer" title="Open in new tab">↗</a>`
    : "";
  return `
    <div class="doc-row">
      <input type="text" placeholder="URL (Confluence pages are auto-fetched)" value="${escapeHtml(doc.url || "")}" data-doc-idx="${idx}" data-doc-field="url" />
      <input type="text" placeholder="Label (optional)" value="${escapeHtml(doc.label || "")}" data-doc-idx="${idx}" data-doc-field="label" />
      ${badge}
      ${openLink}
      <button type="button" class="link-btn doc-remove" data-doc-action="remove" data-doc-idx="${idx}" title="Remove">×</button>
    </div>
  `;
}

function addEditingDoc() {
  ideaState.editingDocs = ideaState.editingDocs || [];
  ideaState.editingDocs.push({ url: "", label: "" });
  renderEditingDocs();
  // Focus the URL input of the new row
  setTimeout(() => {
    const rows = $("idea-edit-docs").querySelectorAll(".doc-row");
    const last = rows[rows.length - 1];
    last?.querySelector('input[data-doc-field="url"]')?.focus();
  }, 50);
}

function removeEditingDoc(idx) {
  if (!ideaState.editingDocs) return;
  ideaState.editingDocs.splice(idx, 1);
  renderEditingDocs();
}

async function saveIdeaEdit() {
  const id = ideaState.editing;
  if (!id) return;
  const before = ideaState.ideas.find((i) => i.id === id);  // snapshot for promote detection
  const title = $("idea-edit-title").value.trim();
  if (!title) {
    $("idea-edit-status").className = "status error";
    $("idea-edit-status").textContent = "title required";
    return;
  }
  $("idea-edit-status").className = "status";
  $("idea-edit-status").textContent = "saving…";
  const newStatus = $("idea-edit-status-select").value;
  try {
    const docsPayload = (ideaState.editingDocs || [])
      .filter((d) => (d.url || "").trim())
      .map((d) => ({ url: d.url.trim(), label: (d.label || "").trim() || null }));
    const resp = await fetch(`/api/ideas/${encodeURIComponent(id)}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        title,
        notes: $("idea-edit-notes").value,
        stakeholder: $("idea-edit-stakeholder").value.trim() || null,
        status: newStatus,
        documents: docsPayload,
        segments: readSegmentToggles("idea-edit-segments"),
      }),
    });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({ detail: resp.statusText }));
      throw new Error(err.detail || resp.statusText);
    }
    closeIdeaEdit();
    await loadIdeas();
    // Any transition INTO promoted triggers the Manage requirements prefill,
    // regardless of how the status change happened.
    if (newStatus === "promoted" && before?.status !== "promoted") {
      const updated = ideaState.ideas.find((i) => i.id === id);
      if (updated) jumpToManageRequirements(updated);
    }
  } catch (e) {
    $("idea-edit-status").className = "status error";
    $("idea-edit-status").textContent = `error: ${e.message}`;
  }
}

async function deleteIdeaFromModal() {
  const id = ideaState.editing;
  if (!id) return;
  const idea = ideaState.ideas.find((i) => i.id === id);
  if (!idea) return;
  if (!confirm(`Delete idea "${idea.title}"?`)) return;
  try {
    const resp = await fetch(`/api/ideas/${encodeURIComponent(id)}`, { method: "DELETE" });
    if (!resp.ok) throw new Error(resp.statusText);
    closeIdeaEdit();
    await loadIdeas();
  } catch (e) {
    $("idea-edit-status").className = "status error";
    $("idea-edit-status").textContent = `delete failed: ${e.message}`;
  }
}

async function deleteIdea(id) {
  const idea = ideaState.ideas.find((i) => i.id === id);
  if (!idea) return;
  if (!confirm(`Delete idea "${idea.title}"?`)) return;
  try {
    const resp = await fetch(`/api/ideas/${encodeURIComponent(id)}`, { method: "DELETE" });
    if (!resp.ok) throw new Error(resp.statusText);
    await loadIdeas();
  } catch (e) {
    alert(`Delete failed: ${e.message}`);
  }
}

async function promoteIdea(id) {
  const idea = ideaState.ideas.find((i) => i.id === id);
  if (!idea) return;
  // Server-side: set status=promoted (no-op if already promoted).
  try {
    await fetch(`/api/ideas/${encodeURIComponent(id)}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ status: "promoted" }),
    });
  } catch {}
  jumpToManageRequirements(idea);
}

// Prefill Manage requirements with the idea's context + remember the source
// idea so we can link its metadata to the newly created Epic after apply.
// Cached doc bodies are prepended so Claude has the full background.
const TOTAL_DOC_BUDGET = 30000;  // chars across all docs included in context

function jumpToManageRequirements(idea) {
  const parts = [`# ${idea.title}`];
  if (idea.notes) parts.push("", idea.notes);
  if (idea.stakeholder) parts.push("", `Stakeholder: ${idea.stakeholder}`);

  const docs = idea.documents || [];
  let docBudget = TOTAL_DOC_BUDGET;
  for (const doc of docs) {
    const heading = `## From: ${doc.label || doc.url}`;
    if (doc.cached_text) {
      let body = doc.cached_text;
      if (body.length > docBudget) {
        body = body.slice(0, docBudget) + `\n\n... [truncated to fit total doc budget]`;
      }
      parts.push("", heading, "", body);
      docBudget -= body.length;
      if (docBudget <= 0) break;
    } else if (doc.fetch_error) {
      parts.push("", heading, `(could not fetch: ${doc.fetch_error}) - link only: ${doc.url}`);
    } else {
      parts.push("", heading, `Link only: ${doc.url}`);
    }
  }

  $("context").value = parts.join("\n");
  $("tickets-source").value = "";  // empty -> Epic-creation mode
  state.fromIdea = {
    id: idea.id,
    one_pager_url: docs[0]?.url || idea.one_pager_url || null,
    stakeholder: idea.stakeholder || null,
    documents: docs.map((d) => ({ url: d.url, label: d.label })),
  };
  showPage("sync");
  $("context").focus();
}

$("idea-add-btn").addEventListener("click", openAddIdeaForm);
$("idea-cancel-btn").addEventListener("click", closeAddIdeaForm);
$("idea-save-btn").addEventListener("click", saveIdea);
$("idea-board").addEventListener("click", (e) => {
  const action = e.target.dataset.action;
  const id = e.target.dataset.id;
  if (action === "edit-idea") editIdea(id);
  else if (action === "delete-idea") deleteIdea(id);
  else if (action === "promote-idea") promoteIdea(id);
});

// Idea edit modal wiring
$("idea-edit-save").addEventListener("click", saveIdeaEdit);
$("idea-edit-cancel").addEventListener("click", closeIdeaEdit);
$("idea-edit-close").addEventListener("click", closeIdeaEdit);
$("idea-edit-delete").addEventListener("click", deleteIdeaFromModal);
$("idea-edit-add-doc").addEventListener("click", addEditingDoc);
$("idea-edit-docs").addEventListener("click", (e) => {
  if (e.target.dataset.docAction === "remove") {
    removeEditingDoc(+e.target.dataset.docIdx);
  }
});
$("idea-edit-docs").addEventListener("input", (e) => {
  const idx = e.target.dataset.docIdx;
  const field = e.target.dataset.docField;
  if (idx === undefined || !field) return;
  if (!ideaState.editingDocs[+idx]) return;
  ideaState.editingDocs[+idx][field] = e.target.value;
});
$("idea-edit-modal").addEventListener("click", (e) => {
  // Click on backdrop closes the modal; clicks inside the card don't bubble here.
  if (e.target.id === "idea-edit-modal") closeIdeaEdit();
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !$("idea-edit-modal").classList.contains("hidden")) {
    closeIdeaEdit();
  }
});

// ---------- Analysis warm-up (shared between Actions + PPR) ----------

// Auto-fires on first Actions/PPR visit if some tracked epics lack analysis
// in cache. Polls every 5s and refreshes the active tab as each finishes.
const warmupState = {
  polling: false,
  refreshCallbacks: new Set(),  // functions to call when state changes
};

async function fetchWarmupStatus() {
  try {
    const resp = await fetch("/api/analyze-missing/status");
    if (!resp.ok) return null;
    return await resp.json();
  } catch { return null; }
}

async function ensureWarmupStarted() {
  // If anything is missing and nothing's in progress, trigger.
  const s = await fetchWarmupStatus();
  if (!s) return null;
  if (s.in_progress) {
    startWarmupPolling();
    return s;
  }
  if ((s.missing || []).length === 0) return s;
  // Auth required - if anon, silently skip (user can't trigger anyway)
  if (!statusState.authenticated) return s;
  try {
    await fetch("/api/analyze-missing", { method: "POST" });
  } catch {}
  startWarmupPolling();
  return s;
}

function startWarmupPolling() {
  if (warmupState.polling) return;
  warmupState.polling = true;
  const tick = async () => {
    const s = await fetchWarmupStatus();
    if (!s) {
      warmupState.polling = false;
      return;
    }
    renderWarmupBanner(s);
    // Fire any registered tab-refresh handlers each tick so card chips +
    // group lists pick up newly-analyzed epics as they land.
    warmupState.refreshCallbacks.forEach((fn) => { try { fn(); } catch {} });
    if (s.in_progress) {
      setTimeout(tick, 5000);
    } else {
      warmupState.polling = false;
      // One last refresh after completion
      warmupState.refreshCallbacks.forEach((fn) => { try { fn(); } catch {} });
    }
  };
  tick();
}

function renderWarmupBanner(status) {
  // Banner hosts live inside each tab's main; both render so it shows up
  // immediately when the user switches tabs.
  document.querySelectorAll(".warmup-banner-host").forEach((host) => {
    renderWarmupBannerInto(host, status);
  });
}

function renderWarmupBannerInto(host, status) {
  if (!host) return;
  const pending = (status.pending || []).length;
  const missing = (status.missing || []).length;
  const total = status.total_tracked || 0;
  if (!status.in_progress && missing === 0) {
    host.innerHTML = "";
    return;
  }
  if (status.in_progress) {
    const done = total - missing;
    host.innerHTML = `
      <div class="refresh-banner warmup">
        <span class="refresh-banner-text">
          <span class="spinner"></span>
          Analyzing projects: ${escapeHtml(String(done))}/${escapeHtml(String(total))} ready (${escapeHtml(String(pending))} in flight)
        </span>
      </div>
    `;
  } else if (missing > 0) {
    host.innerHTML = `
      <div class="refresh-banner warmup">
        <span class="refresh-banner-text">${escapeHtml(String(missing))} project${missing === 1 ? "" : "s"} not yet analyzed.</span>
        <div class="refresh-banner-actions">
          <button type="button" data-action="trigger-warmup">Analyze now</button>
        </div>
      </div>
    `;
  }
}

async function triggerWarmupManually() {
  if (!statusState.authenticated) return;
  await fetch("/api/analyze-missing", { method: "POST" }).catch(() => {});
  startWarmupPolling();
}


// ---------- Actions tab ----------

const actionsState = {
  groups: [],
  mineOnly: false,
  showDone: false,
  openClosure: null,  // {epic_key, sig} when a closure-reason form is open
};

async function loadActions() {
  $("actions-list").innerHTML = `<p class="status">loading…</p>`;
  try {
    const resp = await fetch("/api/actions");
    if (!resp.ok) throw new Error(resp.statusText);
    const data = await resp.json();
    actionsState.groups = data.groups || [];
    renderActionsTab();
  } catch (e) {
    $("actions-list").innerHTML = `<p class="status error">failed to load: ${escapeHtml(e.message)}</p>`;
  }
  // Auto-warm the cache so all tracked epics' actions show up over the next
  // few minutes; register a callback so we re-render as each completes.
  warmupState.refreshCallbacks.add(loadActions);
  ensureWarmupStarted();
}

function renderActionsTab() {
  const host = $("actions-list");
  const groups = actionsState.groups;

  // Apply filters: hide done unless toggled; if mineOnly, keep only for_user.
  const filtered = groups.map((g) => {
    const filteredActions = g.actions.filter((a) => {
      if (!actionsState.showDone && a.done) return false;
      if (actionsState.mineOnly && !a.for_user) return false;
      return true;
    });
    return { ...g, _actions: filteredActions };
  }).filter((g) => g._actions.length > 0);

  const totalShown = filtered.reduce((acc, g) => acc + g._actions.length, 0);
  const totalAll = groups.reduce((acc, g) => acc + g.actions.length, 0);

  if (!groups.length) {
    host.innerHTML = `<p class="empty-state">No tracked projects have action items yet. Open a project on the Projects Dashboard to generate analysis.</p>`;
    return;
  }
  if (!filtered.length) {
    host.innerHTML = `<p class="empty-state">No action items match the current filter. Try toggling "Only mine" or "Show done".</p>`;
    return;
  }

  // Sort actions within each group: open first; manual first; for_user first; then urgency.
  const urg = { high: 0, medium: 1, low: 2 };
  for (const g of filtered) {
    g._actions.sort((a, b) => {
      if (a.done !== b.done) return a.done ? 1 : -1;
      if ((a.source === "manual") !== (b.source === "manual")) return a.source === "manual" ? -1 : 1;
      if (a.for_user !== b.for_user) return a.for_user ? -1 : 1;
      return (urg[a.urgency] ?? 9) - (urg[b.urgency] ?? 9);
    });
  }

  const header = `<div class="actions-meta-line">Showing ${totalShown} of ${totalAll} actions</div>`;

  host.innerHTML = header + filtered.map((g) => `
    <div class="actions-group">
      <div class="actions-group-head">
        <h3>
          <span class="key">${escapeHtml(g.epic_key)}</span>
          ${jiraLinkIcon(g.epic_key, g.epic_summary)}
          <span class="actions-group-summary">${escapeHtml(g.epic_summary)}</span>
          ${g.epic_assessment ? `<span class="assessment assessment-${g.epic_assessment}">${escapeHtml(g.epic_assessment)}</span>` : ""}
        </h3>
        <span class="count">${g._actions.length} shown</span>
      </div>
      ${g._actions.map((a) => actionListItem(a, g.epic_key)).join("")}
    </div>
  `).join("");
}

function actionListItem(a, epicKey) {
  const cls = ["analysis-item", `urgency-${a.urgency}`];
  if (a.for_user) cls.push("for-user");
  if (a.source === "manual") cls.push("manual");
  if (a.done) cls.push("action-done");
  const sig = a.sig || "";
  const closureOpen = sig
    && actionsState.openClosure?.epic_key === epicKey
    && actionsState.openClosure?.sig === sig;

  let actionsHtml = "";
  if (a.done) {
    actionsHtml = `<button class="link-btn detail-link" data-action="actions-reopen" data-epic-key="${escapeHtml(epicKey)}" data-sig="${escapeHtml(sig)}">reopen</button>`;
  } else if (sig) {
    const forMeLabel = a.for_user ? "not for me" : "for me";
    actionsHtml = `
      <button class="link-btn detail-link" data-action="actions-open-close" data-epic-key="${escapeHtml(epicKey)}" data-sig="${escapeHtml(sig)}">mark done</button>
      <button class="link-btn detail-link" data-action="actions-toggle-for-user" data-epic-key="${escapeHtml(epicKey)}" data-sig="${escapeHtml(sig)}" data-current="${a.for_user ? "1" : "0"}">${forMeLabel}</button>
      <button class="link-btn detail-link" data-action="actions-create-stub" data-epic-key="${escapeHtml(epicKey)}">+ ticket</button>
    `;
  }
  const removeBtn = (a.source === "manual" && a.manual_index !== null && a.manual_index !== undefined && !a.done)
    ? `<button class="link-btn detail-link" data-action="actions-remove-manual" data-epic-key="${escapeHtml(epicKey)}" data-idx="${a.manual_index}" title="Delete this manual action permanently">delete</button>`
    : "";

  return `
    <div class="${cls.join(" ")}" data-epic-key="${escapeHtml(epicKey)}" data-sig="${escapeHtml(sig)}">
      <div class="title">
        ${a.source === "manual" ? '<span class="source-badge">manual</span>' : ""}
        ${a.for_user ? '<span class="for-you">for you</span>' : ""}
        <span class="urgency urgency-${a.urgency}">${a.urgency}</span>
        ${escapeHtml(a.title)}
        ${actionsHtml}
        ${removeBtn}
      </div>
      <div class="detail">${escapeHtml(a.detail || "")}</div>
      ${(a.ticket_keys || []).length ? `<div class="refs">→ ${a.ticket_keys.map(ticketKey).join(", ")}</div>` : ""}
      ${closureOpen ? actionsClosureFormHtml(epicKey, sig) : ""}
    </div>
  `;
}

function actionsClosureFormHtml(epicKey, sig) {
  return `
    <div class="closure-form">
      <textarea
        data-action="actions-reason-input"
        rows="2"
        placeholder="Why mark done? (e.g. 'already covered by FE-X', 'not relevant'). Optional but useful - the prompt-refinement skill reads this."
      ></textarea>
      <div class="form-actions">
        <button data-action="actions-confirm-close" data-epic-key="${escapeHtml(epicKey)}" data-sig="${escapeHtml(sig)}">Confirm</button>
        <button class="secondary" data-action="actions-cancel-close">Cancel</button>
      </div>
    </div>
  `;
}

// Click + input delegation for the Actions tab
$("page-actions").addEventListener("click", (e) => {
  const target = e.target.closest("[data-action]");
  if (!target) return;
  const a = target.dataset.action;
  const epicKey = target.dataset.epicKey;
  const sig = target.dataset.sig;

  if (a === "actions-open-close") {
    actionsState.openClosure = { epic_key: epicKey, sig };
    renderActionsTab();
  } else if (a === "actions-cancel-close") {
    actionsState.openClosure = null;
    renderActionsTab();
  } else if (a === "actions-confirm-close") {
    actionsConfirmClose(epicKey, sig);
  } else if (a === "actions-reopen") {
    actionsToggleDone(epicKey, sig, false);
  } else if (a === "actions-remove-manual") {
    actionsRemoveManual(epicKey, +target.dataset.idx);
  } else if (a === "actions-create-stub") {
    createStubTicket(epicKey, target);
  } else if (a === "actions-toggle-for-user") {
    toggleActionForUser(epicKey, target.dataset.sig, target.dataset.current !== "1");
  } else if (a === "trigger-warmup") {
    triggerWarmupManually();
  }
});

$("page-ppr").addEventListener("click", (e) => {
  const target = e.target.closest("[data-action]");
  if (!target) return;
  if (target.dataset.action === "trigger-warmup") {
    triggerWarmupManually();
  }
});

$("page-actions").addEventListener("change", (e) => {
  if (e.target.id === "actions-mine-only") {
    actionsState.mineOnly = e.target.checked;
    renderActionsTab();
  } else if (e.target.id === "actions-show-done") {
    actionsState.showDone = e.target.checked;
    renderActionsTab();
  }
});

async function actionsConfirmClose(epicKey, sig) {
  const ta = document.querySelector('[data-action="actions-reason-input"]');
  const reason = ta ? ta.value.trim() : "";
  try {
    const resp = await fetch(
      `/api/tracked/${encodeURIComponent(epicKey)}/actions/${encodeURIComponent(sig)}/done`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ reason: reason || null }),
      },
    );
    if (!resp.ok) throw new Error(resp.statusText);
    // Patch local state so we don't refetch
    for (const g of actionsState.groups) {
      if (g.epic_key !== epicKey) continue;
      const item = g.actions.find((x) => x.sig === sig);
      if (item) item.done = true;
    }
    actionsState.openClosure = null;
    renderActionsTab();
  } catch (e) {
    alert(`Close failed: ${e.message}`);
  }
}

async function actionsToggleDone(epicKey, sig, done) {
  try {
    const resp = await fetch(
      `/api/tracked/${encodeURIComponent(epicKey)}/actions/${encodeURIComponent(sig)}/done`,
      { method: done ? "POST" : "DELETE" },
    );
    if (!resp.ok) throw new Error(resp.statusText);
    for (const g of actionsState.groups) {
      if (g.epic_key !== epicKey) continue;
      const item = g.actions.find((x) => x.sig === sig);
      if (item) item.done = done;
    }
    renderActionsTab();
  } catch (e) {
    alert(`Update failed: ${e.message}`);
  }
}

async function actionsRemoveManual(epicKey, index) {
  if (!confirm("Delete this manual action permanently?")) return;
  try {
    const resp = await fetch(
      `/api/tracked/${encodeURIComponent(epicKey)}/actions/${index}`,
      { method: "DELETE" },
    );
    if (!resp.ok) throw new Error(resp.statusText);
    // Refetch since indexes shift after delete
    await loadActions();
  } catch (e) {
    alert(`Delete failed: ${e.message}`);
  }
}

// ---------- PPR (Project Portfolio Review) ----------

const pprState = {
  groups: [],
  recent_window_days: 60,
  segmentFilter: "all",  // "all" | "business" | "school" | "home" | "students"
};

async function loadPPR() {
  $("ppr-list").innerHTML = `<p class="status loading"><span class="spinner"></span> building portfolio summary…</p>`;
  try {
    const resp = await fetch("/api/ppr");
    if (!resp.ok) throw new Error(resp.statusText);
    const data = await resp.json();
    pprState.groups = data.groups || [];
    pprState.recent_window_days = data.recent_window_days || 60;
    renderPPR();
  } catch (e) {
    $("ppr-list").innerHTML = `<p class="status error">failed to load: ${escapeHtml(e.message)}</p>`;
  }
  warmupState.refreshCallbacks.add(loadPPR);
  ensureWarmupStarted();
}

function renderPPR() {
  renderPPRSegmentFilter();
  const host = $("ppr-list");
  const filter = pprState.segmentFilter;

  const visibleGroups = filter === "all"
    ? pprState.groups
    : pprState.groups.filter((g) => g.segment === filter);

  const totalShown = visibleGroups.reduce((acc, g) => acc + (g.projects || []).length, 0);
  const totalAll = pprState.groups.reduce((acc, g) => acc + (g.projects || []).length, 0);

  if (totalAll === 0) {
    host.innerHTML = `<p class="empty-state">No tracked projects yet. Add some on the Projects Dashboard.</p>`;
    return;
  }

  host.innerHTML = `
    <div class="actions-meta-line">Showing ${totalShown} of ${totalAll} items${filter !== "all" ? ` (segment: ${escapeHtml(filter)})` : ""}</div>
    ${visibleGroups.map(pprGroupHtml).join("")}
  `;
}

function renderPPRSegmentFilter() {
  // Build filter options from groups present in the response so we don't
  // show buttons for empty segments.
  const present = new Set(pprState.groups.map((g) => g.segment));
  const SEGMENTS = ["all", "business", "school", "home", "students", "internal", "other"].filter(
    (s) => s === "all" || present.has(s),
  );
  $("ppr-segment-filter").innerHTML = SEGMENTS.map((s) => `
    <button type="button"
            class="segment-toggle segment-${s === "all" ? "any" : s} ${pprState.segmentFilter === s ? "active" : ""}"
            data-action="ppr-set-segment" data-segment="${s}">
      ${escapeHtml(s)}
    </button>
  `).join("");
}

function pprGroupHtml(g) {
  const items = g.projects || [];
  return `
    <div class="ppr-group ppr-segment-${escapeHtml(g.segment)}">
      <div class="ppr-group-head">
        <h3>${escapeHtml(g.label)}</h3>
        <span class="count">${items.length}</span>
      </div>
      ${items.length === 0
        ? '<p class="empty-state">Nothing here yet.</p>'
        : `<div class="ppr-rows">${items.map(pprRow).join("")}</div>`}
    </div>
  `;
}

const PPR_STAGE_LABEL = {
  preparation: "in preparation",
  development: "in development",
  recently_completed: "recently completed",
};

function pprRow(p) {
  const counts = p.counts || {};
  const total = counts.total || 0;
  const donePct = total ? (counts.done / total) * 100 : 0;
  const ipPct = total ? (counts.in_progress / total) * 100 : 0;
  const tdPct = total ? (counts.to_do / total) * 100 : 0;
  const isIdea = p.kind === "idea";
  const stageLabel = isIdea ? "in preparation for dev" : (PPR_STAGE_LABEL[p.stage] || p.stage);
  const summaryText = p.stakeholder_summary
    || (isIdea ? "(no notes yet)" : "(no AI analysis yet - open the project on Projects Dashboard to generate one, or click Refresh analysis)");
  return `
    <div class="ppr-row ppr-row-${isIdea ? "idea" : "project"}">
      <div class="ppr-row-head">
        <span class="ppr-stage-badge ppr-stage-${escapeHtml(p.stage)}">${escapeHtml(stageLabel)}</span>
        ${isIdea ? "" : `<span class="key">${escapeHtml(p.key)}</span>${jiraLinkIcon(p.key, p.summary)}`}
        <span class="ppr-row-title">${escapeHtml(p.summary)}</span>
        ${p.assessment ? `<span class="assessment assessment-${p.assessment}">${escapeHtml(p.assessment)}</span>` : ""}
        ${!isIdea && total ? `<span class="ppr-progress">${p.progress_pct}%</span>` : ""}
        ${p.stakeholder ? `<span class="idea-stakeholder">${escapeHtml(p.stakeholder)}</span>` : ""}
        ${p.one_pager_url ? `<a class="idea-onepager" href="${escapeHtml(p.one_pager_url)}" target="_blank" rel="noopener noreferrer">one-pager ↗</a>` : ""}
      </div>
      <div class="ppr-row-summary">${escapeHtml(summaryText)}</div>
      ${!isIdea && total ? `
        <div class="progress-bar">
          ${donePct > 0 ? `<div class="done" style="width:${donePct}%"></div>` : ""}
          ${ipPct > 0 ? `<div class="in-progress" style="width:${ipPct}%"></div>` : ""}
          ${tdPct > 0 ? `<div class="to-do" style="width:${tdPct}%"></div>` : ""}
        </div>` : ""}
      ${(p.segments || []).length ? `<div class="ppr-segments">${p.segments.map((s) => `<span class="segment-chip segment-${escapeHtml(s)}">${escapeHtml(s)}</span>`).join("")}</div>` : ""}
    </div>
  `;
}

$("page-ppr").addEventListener("click", (e) => {
  const target = e.target.closest("[data-action]");
  if (!target) return;
  if (target.dataset.action === "ppr-set-segment") {
    pprState.segmentFilter = target.dataset.segment;
    renderPPR();
  }
});

// Kick off auth check on load - sets the body data-auth attribute before
// pages render, so anon users see read-only mode without a flash.
refreshAuthStatus();
