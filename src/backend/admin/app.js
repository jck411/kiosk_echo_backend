// Minimal vanilla admin UI — no build step.

const $ = (sel) => document.querySelector(sel);

async function api(path, init) {
    const res = await fetch(path, {
        headers: { "Content-Type": "application/json" },
        ...init,
    });
    if (!res.ok) {
        const text = await res.text();
        throw new Error(`${res.status} ${res.statusText}: ${text}`);
    }
    if (res.status === 204) return null;
    return res.json();
}

function setStatus(el, msg, kind) {
    el.textContent = msg;
    el.className = "status " + (kind || "");
    if (kind === "ok") {
        setTimeout(() => {
            if (el.textContent === msg) {
                el.textContent = "";
                el.className = "status";
            }
        }, 2500);
    }
}

// -------- Tab switching --------
document.querySelectorAll("nav button").forEach((btn) => {
    btn.addEventListener("click", () => {
        document.querySelectorAll("nav button").forEach((b) => b.classList.remove("active"));
        document.querySelectorAll(".tab").forEach((t) => t.classList.remove("active"));
        btn.classList.add("active");
        document.getElementById(btn.dataset.tab).classList.add("active");
    });
});

// =============== System Prompt ===============
async function loadPrompt() {
    const status = $("#prompt-status");
    setStatus(status, "Loading...");
    try {
        const llm = await api("/api/settings/llm");
        $("#prompt-model").value = llm.model || "";
        $("#prompt-text").value = llm.system_prompt || "";
        setStatus(status, "Loaded.", "ok");
    } catch (e) {
        setStatus(status, e.message, "err");
    }
}

$("#prompt-save").addEventListener("click", async () => {
    const status = $("#prompt-status");
    setStatus(status, "Saving...");
    try {
        await api("/api/settings/llm", {
            method: "PUT",
            body: JSON.stringify({
                model: $("#prompt-model").value,
                system_prompt: $("#prompt-text").value,
            }),
        });
        setStatus(status, "Saved.", "ok");
    } catch (e) {
        setStatus(status, e.message, "err");
    }
});

$("#prompt-reset").addEventListener("click", async () => {
    if (!confirm("Reset LLM settings to defaults?")) return;
    const status = $("#prompt-status");
    setStatus(status, "Resetting...");
    try {
        await api("/api/settings/llm/reset", { method: "POST" });
        await loadPrompt();
        setStatus(status, "Reset.", "ok");
    } catch (e) {
        setStatus(status, e.message, "err");
    }
});

// =============== MCP ===============
async function loadMcp() {
    const status = $("#mcp-status");
    setStatus(status, "Loading...");
    try {
        const [serversResp, prefs] = await Promise.all([
            api("/api/mcp/servers/"),
            api("/api/mcp/preferences"),
        ]);
        renderMcpTable(serversResp.servers || [], prefs);
        setStatus(status, "Loaded.", "ok");
    } catch (e) {
        setStatus(status, e.message, "err");
    }
}

function renderMcpTable(servers, prefs) {
    const configured = prefs?.enabled_servers;
    const allowedSet = new Set(
        configured == null ? servers.map((server) => server.id) : configured
    );
    const tbody = $("#mcp-table tbody");
    tbody.innerHTML = "";
    servers.forEach((s) => {
        const tr = document.createElement("tr");
        const tools = (s.tools || []).map((t) => t.name || t).join(", ");
        const statusKind = s.connected
            ? "connected"
            : s.enabled
                ? "disconnected"
                : "disabled";
        tr.innerHTML = `
      <td><strong>${s.id}</strong><br><span style="color:var(--muted);font-size:11px">${s.url || ""}</span></td>
      <td class="status-cell ${statusKind}">${statusKind}</td>
      <td><input type="checkbox" data-allowed="${s.id}" ${allowedSet.has(s.id) ? "checked" : ""}></td>
      <td class="tools">${tools || "<em style='color:var(--muted)'>(none / not connected)</em>"}</td>
    `;
        tbody.appendChild(tr);
    });
}

$("#mcp-refresh").addEventListener("click", async () => {
    const status = $("#mcp-status");
    setStatus(status, "Refreshing...");
    try {
        await api("/api/mcp/servers/refresh", { method: "POST" });
        await loadMcp();
        setStatus(status, "Refreshed.", "ok");
    } catch (e) {
        setStatus(status, e.message, "err");
    }
});

$("#mcp-save").addEventListener("click", async () => {
    const status = $("#mcp-status");
    setStatus(status, "Saving...");
    try {
        const allowed = Array.from(
            document.querySelectorAll("#mcp-table input[data-allowed]:checked")
        ).map((cb) => cb.dataset.allowed);
        await api("/api/mcp/preferences", {
            method: "PUT",
            body: JSON.stringify({ enabled_servers: allowed }),
        });
        setStatus(status, "Saved.", "ok");
    } catch (e) {
        setStatus(status, e.message, "err");
    }
});

// =============== Raw kiosk settings ===============
function settingsPath() {
    const section = $("#settings-section").value;
    return `/api/settings/${section}`;
}

$("#settings-load").addEventListener("click", async () => {
    const status = $("#settings-status");
    setStatus(status, "Loading...");
    try {
        const data = await api(settingsPath());
        $("#settings-json").value = JSON.stringify(data, null, 2);
        setStatus(status, "Loaded.", "ok");
    } catch (e) {
        setStatus(status, e.message, "err");
    }
});

$("#settings-save").addEventListener("click", async () => {
    const status = $("#settings-status");
    setStatus(status, "Saving...");
    let body;
    try {
        body = JSON.parse($("#settings-json").value);
    } catch (e) {
        setStatus(status, "Invalid JSON: " + e.message, "err");
        return;
    }
    try {
        await api(settingsPath(), {
            method: "PUT",
            body: JSON.stringify(body),
        });
        setStatus(status, "Saved.", "ok");
    } catch (e) {
        setStatus(status, e.message, "err");
    }
});

$("#settings-reset").addEventListener("click", async () => {
    if (!confirm("Reset this section to defaults?")) return;
    const status = $("#settings-status");
    setStatus(status, "Resetting...");
    try {
        await api(`${settingsPath()}/reset`, { method: "POST" });
        const data = await api(settingsPath());
        $("#settings-json").value = JSON.stringify(data, null, 2);
        setStatus(status, "Reset.", "ok");
    } catch (e) {
        setStatus(status, e.message, "err");
    }
});

// =============== Health ===============
async function loadHealth() {
    try {
        const data = await api("/health");
        $("#health-output").textContent = JSON.stringify(data, null, 2);
    } catch (e) {
        $("#health-output").textContent = "Error: " + e.message;
    }
}

$("#health-refresh").addEventListener("click", loadHealth);

// =============== Initial loads ===============
loadPrompt();
loadMcp();
loadHealth();
