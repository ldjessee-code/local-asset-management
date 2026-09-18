(() => {
  const $ = (id) => document.getElementById(id);
  const tokenKey = "lamToken";
  const themeKey = "lamTheme";
  const langKey = "lamLang";
  let layouts = ["keep_relpath", "by_year", "by_year_month", "pack_tree", "by_ext"];
  let overview = { sources: [] };
  let selectedSourceIds = [];
  let lastLogLen = 0;

  function token() {
    const params = new URLSearchParams(location.search);
    const fromUrl = params.get("token");
    if (fromUrl) {
      sessionStorage.setItem(tokenKey, fromUrl);
    }
    return sessionStorage.getItem(tokenKey) || "";
  }

  async function api(path, opts = {}) {
    const headers = Object.assign({ "X-Lam-Token": token() }, opts.headers || {});
    if (opts.body && !headers["Content-Type"]) headers["Content-Type"] = "application/json";
    const res = await fetch(path, Object.assign({}, opts, { headers }));
    if (res.status === 401) {
      $("auth-card").hidden = false;
      $("auth-error").hidden = false;
      $("auth-error").textContent = t("unauthorized");
      throw new Error("unauthorized");
    }
    const ct = res.headers.get("content-type") || "";
    if (ct.includes("application/json")) {
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || res.statusText);
      return data;
    }
    const text = await res.text();
    if (!res.ok) throw new Error(text || res.statusText);
    return text;
  }

  function bytes(n) {
    if (n == null) return "—";
    const units = ["B", "KB", "MB", "GB", "TB"];
    let v = Number(n);
    let i = 0;
    while (v >= 1024 && i < units.length - 1) {
      v /= 1024;
      i += 1;
    }
    return i === 0 ? `${v} ${units[i]}` : `${v.toFixed(1)} ${units[i]}`;
  }

  function pushMessage(text) {
    if (!text) return;
    const box = $("messages");
    const ph = $("msg-placeholder");
    if (ph) ph.remove();
    const nearBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 40;
    const el = document.createElement("div");
    el.className = "msg";
    const time = document.createElement("time");
    time.dateTime = new Date().toISOString();
    time.textContent = new Date().toLocaleTimeString();
    el.appendChild(time);
    el.appendChild(document.createTextNode(text));
    box.appendChild(el);
    while (box.children.length > 200) box.removeChild(box.firstChild);
    if (nearBottom) box.scrollTop = box.scrollHeight;
  }

  function setPill(state) {
    const el = $("job-pill");
    el.textContent = t("job_" + (state || "idle")) || state;
    el.className = "pill " + (state || "idle");
  }

  function setTheme(name) {
    document.documentElement.setAttribute("data-theme", name);
    localStorage.setItem(themeKey, name);
    $("theme-select").value = name;
  }

  function setLang(lang) {
    document.documentElement.lang = lang;
    localStorage.setItem(langKey, lang);
    applyI18n();
  }

  function setSetupMode(on) {
    document.body.classList.toggle("needs-setup", on);
    $("setup-card").hidden = !on;
  }

  function addSourceRow(source) {
    const row = document.createElement("div");
    row.className = "source-row";
    const path = document.createElement("input");
    path.type = "text";
    path.className = "source-path";
    path.setAttribute("aria-label", t("source_path"));
    path.value = (source && source.path) || "";
    const layout = document.createElement("select");
    layout.className = "source-layout";
    layout.setAttribute("aria-label", t("layout"));
    layouts.forEach((name) => {
      const opt = document.createElement("option");
      opt.value = name;
      opt.textContent = name;
      if (source && source.layout === name) opt.selected = true;
      layout.appendChild(opt);
    });
    if (!source || !source.layout) layout.value = "keep_relpath";
    const priority = document.createElement("input");
    priority.type = "number";
    priority.className = "source-priority";
    priority.setAttribute("aria-label", t("priority"));
    priority.value = source && source.priority != null ? source.priority : 10;
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "ghost";
    remove.textContent = t("remove");
    remove.addEventListener("click", () => row.remove());
    row.append(path, layout, priority, remove);
    $("source-rows").appendChild(row);
  }

  function formPayload() {
    const sources = [...document.querySelectorAll(".source-row")]
      .map((row) => ({
        path: row.querySelector(".source-path").value.trim(),
        layout: row.querySelector(".source-layout").value,
        priority: Number(row.querySelector(".source-priority").value || 0),
      }))
      .filter((s) => s.path);
    const payload = {
      config_path: $("save-path").value.trim(),
      output_folder: $("output-folder").value.trim(),
      sources,
    };
    const q = $("quarantine-path") && $("quarantine-path").value.trim();
    if (q) payload.quarantine = q;
    return payload;
  }

  function columnCount() {
    const w = $("dup-grid").clientWidth || $("main-controls").clientWidth || 640;
    const nsrc = (overview.sources || []).length;
    if (!nsrc) return 2;
    return Math.max(1, Math.min(nsrc, Math.max(2, Math.floor(w / 200))));
  }

  function ensureSelectedIds() {
    const sources = overview.sources || [];
    const n = columnCount();
    const preferred = sources.filter((s) => s.has_duplicates).concat(sources);
    const ids = [];
    for (const s of preferred) {
      if (ids.length >= n) break;
      if (!ids.includes(s.id)) ids.push(s.id);
    }
    while (ids.length < n && sources[ids.length]) ids.push(sources[ids.length].id);
    selectedSourceIds = ids.slice(0, n);
  }

  function flagLabel(flags) {
    if (!flags || !flags.length) return t("empty_cell");
    if (flags.includes("meta_mismatch")) return t("dup_meta_diff");
    if (flags.includes("in_library")) return t("in_library");
    if (flags.includes("dup_hash")) return t("dup_hash");
    if (flags.includes("dup_meta")) return t("dup_meta");
    return t("unique");
  }

  function cellClass(cell) {
    if (!cell) return "dup-cell empty";
    const flags = cell.flags || [];
    const bits = ["dup-cell"];
    if (flags.includes("meta_mismatch")) bits.push("meta_mismatch");
    else if (flags.includes("in_library")) bits.push("in_library");
    else if (flags.includes("dup_hash") || flags.includes("dup_meta")) bits.push("dup_hash");
    else bits.push("unique");
    return bits.join(" ");
  }

  async function renderDuplicates() {
    const sources = overview.sources || [];
    if (!sources.length) {
      $("dup-grid").innerHTML = "";
      $("dup-headers").innerHTML = "";
      return;
    }
    ensureSelectedIds();
    const qs = selectedSourceIds.join(",");
    let data;
    try {
      data = await api("/api/compare?sources=" + encodeURIComponent(qs));
    } catch (err) {
      pushMessage(String(err.message || err));
      return;
    }
    const cols = selectedSourceIds.length;
    $("dup-headers").style.gridTemplateColumns = `repeat(${cols}, minmax(12.5rem, 1fr))`;
    $("dup-grid").style.gridTemplateColumns = `repeat(${cols}, minmax(12.5rem, 1fr))`;
    $("dup-headers").innerHTML = "";
    selectedSourceIds.forEach((sid, idx) => {
      const src = (data.sources || []).find((s) => s.id === sid) || {};
      const wrap = document.createElement("div");
      wrap.className = "dup-head" + (src.has_duplicates ? "" : " no-dups");
      const sel = document.createElement("select");
      sel.setAttribute("aria-label", t("sources") + " " + (idx + 1));
      (data.all_sources || sources).forEach((s) => {
        const opt = document.createElement("option");
        opt.value = s.id;
        const info = sources.find((x) => x.id === s.id) || s;
        opt.textContent = info.has_duplicates === false ? `${s.name} (${t("no_duplicates")})` : s.name;
        if (s.id === sid) opt.selected = true;
        sel.appendChild(opt);
      });
      sel.addEventListener("change", () => {
        selectedSourceIds[idx] = Number(sel.value);
        renderDuplicates();
      });
      wrap.appendChild(sel);
      if (src.has_duplicates === false) {
        const note = document.createElement("div");
        note.className = "muted";
        note.textContent = t("no_duplicates");
        wrap.appendChild(note);
      }
      $("dup-headers").appendChild(wrap);
    });

    const grid = $("dup-grid");
    grid.innerHTML = "";
    (data.rows || []).forEach((row) => {
      row.cells.forEach((cell, i) => {
        const div = document.createElement("div");
        div.className = cellClass(cell);
        div.setAttribute("role", "cell");
        if (!cell) {
          div.innerHTML = `<span class="sr-only">${t("empty_cell")}</span>`;
        } else {
          const name = document.createElement("div");
          name.textContent = cell.relpath;
          const meta = document.createElement("div");
          meta.className = "flag";
          meta.textContent = flagLabel(cell.flags) + " · " + bytes(cell.size);
          div.append(name, meta);
          if (cell.zip) {
            const zbtn = document.createElement("button");
            zbtn.type = "button";
            zbtn.className = "ghost";
            zbtn.textContent = t("zip_contents");
            zbtn.addEventListener("click", () => showZip(cell.file_id, div));
            div.appendChild(zbtn);
          }
        }
        grid.appendChild(div);
      });
    });
    const saved = bytes(data.space_to_save || overview.space_to_save || 0);
    $("space-to-save").textContent = saved;
    if ($("space-to-save-bar")) $("space-to-save-bar").textContent = saved;
  }

  async function showZip(fileId, host) {
    try {
      const data = await api("/api/zip/" + fileId);
      let list = host.querySelector(".zip-list");
      if (list) {
        list.remove();
        return;
      }
      list = document.createElement("ul");
      list.className = "zip-list";
      (data.entries || []).forEach((e) => {
        const li = document.createElement("li");
        li.textContent = `${e.inner_path} (${bytes(e.size)})`;
        list.appendChild(li);
      });
      if (!data.entries || !data.entries.length) {
        const li = document.createElement("li");
        li.textContent = "—";
        list.appendChild(li);
      }
      host.appendChild(list);
    } catch (err) {
      pushMessage(String(err.message || err));
    }
  }

  function renderTree(el, listing, kind, sourceId) {
    el.innerHTML = "";
    if (listing.missing) {
      el.textContent = t("missing_folder");
      return;
    }
    if (listing.rel) {
      const up = document.createElement("button");
      up.type = "button";
      up.className = "linkish";
      up.textContent = t("up");
      up.addEventListener("click", () => loadBrowse(el, kind, sourceId, listing.parent || ""));
      el.appendChild(up);
    }
    const ul = document.createElement("ul");
    (listing.dirs || []).forEach((d) => {
      const li = document.createElement("li");
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "linkish";
      btn.textContent = d.name + "/";
      btn.addEventListener("click", () => loadBrowse(el, kind, sourceId, d.rel));
      li.appendChild(btn);
      ul.appendChild(li);
    });
    (listing.files || []).forEach((f) => {
      const li = document.createElement("li");
      li.textContent = `${f.name} (${bytes(f.size)})`;
      ul.appendChild(li);
    });
    el.appendChild(ul);
  }

  async function loadBrowse(el, kind, sourceId, rel) {
    const q = new URLSearchParams({ kind, rel: rel || "" });
    if (sourceId) q.set("source_id", String(sourceId));
    try {
      const listing = await api("/api/browse?" + q.toString());
      renderTree(el, listing, kind, sourceId);
    } catch (err) {
      el.textContent = String(err.message || err);
    }
  }

  async function loadOverview() {
    try {
      overview = await api("/api/overview");
    } catch (err) {
      if (String(err.message).includes("Set up")) return;
      pushMessage(String(err.message || err));
      return;
    }
    const last = $("last-scan");
    if (overview.last_scan_end) {
      last.dateTime = overview.last_scan_end;
      last.textContent = new Date(overview.last_scan_end).toLocaleString();
    } else {
      last.textContent = t("never_scanned");
    }
    $("stale-warning").hidden = !overview.stale;
    if (overview.stale) pushMessage(t("scan_stale"));
    $("space-to-save").textContent = bytes(overview.space_to_save || 0);
    if ($("space-to-save-bar")) $("space-to-save-bar").textContent = bytes(overview.space_to_save || 0);
    if ($("bar-scan")) {
      $("bar-scan").textContent = overview.last_scan_end
        ? t("last_scan") + ": " + new Date(overview.last_scan_end).toLocaleString()
        : t("never_scanned");
    }
    if ($("dest-path")) $("dest-path").textContent = overview.library_root || "";
    if ($("quarantine-path-label")) $("quarantine-path-label").textContent = overview.quarantine || "";
    if ($("dest-input")) $("dest-input").value = overview.library_root || "";
    if ($("quarantine-input")) $("quarantine-input").value = overview.quarantine || "";
    const zp = $("zip-pairing");
    zp.textContent = `${t("zip_pairing")}: ${overview.zip_paired || 0} ${t("zip_paired")}, ${overview.zip_only || 0} ${t("zip_only")}`;

    const trees = $("source-trees");
    trees.innerHTML = "";
    (overview.sources || []).forEach((s) => {
      const wrap = document.createElement("div");
      const h = document.createElement("h3");
      h.textContent = `${s.name} · ${s.files} ${t("files")} · ${bytes(s.bytes)}`;
      const box = document.createElement("div");
      const open = document.createElement("button");
      open.type = "button";
      open.className = "ghost";
      open.textContent = t("browse");
      open.setAttribute("aria-expanded", "false");
      if (s.scanned !== false && s.id) {
        open.addEventListener("click", () => {
          const on = open.getAttribute("aria-expanded") === "true";
          open.setAttribute("aria-expanded", on ? "false" : "true");
          if (on) box.innerHTML = "";
          else loadBrowse(box, "source", s.id, "");
        });
        wrap.append(h, open, box);
      } else {
        wrap.append(h);
      }
      trees.appendChild(wrap);
    });
    loadBrowse($("dest-tree"), "dest", null, "");
    loadBrowse($("quarantine-tree"), "quarantine", null, "");
    await renderDuplicates();
  }

  async function refreshStatus() {
    try {
      const st = await api("/api/status");
      setPill(st.state || "idle");
      const log = st.log || [];
      if (log.length > lastLogLen) {
        log.slice(lastLogLen).forEach((line) => pushMessage(line));
        lastLogLen = log.length;
      }
      if (st.state === "idle" || st.state === "done" || st.state === "error") {
        lastLogLen = log.length;
      }
      const prog = st.progress || {};
      const bar = $("progress-bar");
      if (prog.total && prog.done != null) {
        bar.style.width = Math.min(100, (100 * prog.done) / prog.total) + "%";
      } else if (st.state === "running") bar.style.width = "35%";
      else if (st.state === "done") bar.style.width = "100%";
      else if (st.state === "idle") bar.style.width = "0";
      const health = await api("/api/health");
      $("version").textContent = "v" + (health.version || "");
      return st;
    } catch (err) {
      if (err.message !== "unauthorized") setPill("error");
      return null;
    }
  }

  async function runJob(name, body) {
    const opts = { method: "POST" };
    if (body) opts.body = JSON.stringify(body);
    await api("/api/" + name, opts);
    lastLogLen = 0;
    await pollUntilIdle();
    if (name === "scan") {
      $("apply-confirm").checked = false;
      pushMessage(t("scan_finished"));
      await loadOverview();
      if (overview.needs_deep) {
        pushMessage(t("needs_deep"));
        await runJob("deep-scan");
        return;
      }
    }
    if (name === "deep-scan") {
      pushMessage(t("deep_finished"));
      $("apply-confirm").checked = false;
    }
    await loadOverview();
  }

  async function pollUntilIdle() {
    for (let i = 0; i < 600; i += 1) {
      const st = await refreshStatus();
      if (!st || st.state !== "running") return;
      await new Promise((r) => setTimeout(r, 400));
    }
  }

  function outputFolderFromRoot(root) {
    if (!root) return "";
    return root.replace(/[\\/]+media[\\/]*$/, "");
  }

  async function fillSetupDefaults() {
    const d = await api("/api/setup/defaults");
    layouts = d.layouts || layouts;
    let cfg = { needs_setup: true };
    try {
      cfg = await api("/api/config");
    } catch (_err) {
      cfg = { needs_setup: true };
    }
    $("save-path").value = cfg.config_path || d.default_config_path || "";
    if ($("load-path") && cfg.config_path) $("load-path").value = cfg.config_path;
    $("output-folder").value = outputFolderFromRoot(cfg.library_root) || $("output-folder").value;
    if ($("quarantine-path")) $("quarantine-path").value = cfg.quarantine || "";
    $("source-rows").innerHTML = "";
    const srcs = cfg.sources || [];
    if (srcs.length) srcs.forEach((s) => addSourceRow(s));
    else {
      addSourceRow({ path: "", layout: "keep_relpath", priority: 30 });
      addSourceRow({ path: "", layout: "keep_relpath", priority: 10 });
    }
  }

  $("theme-select").addEventListener("change", () => setTheme($("theme-select").value));
  $("lang-select").addEventListener("change", () => setLang($("lang-select").value));
  function setLeftCollapsed(on) {
    document.body.classList.toggle("left-collapsed", on);
    $("toggle-left").setAttribute("aria-expanded", on ? "false" : "true");
    if ($("toggle-left-bar")) $("toggle-left-bar").setAttribute("aria-expanded", on ? "false" : "true");
  }
  function setRightCollapsed(on) {
    document.body.classList.toggle("right-collapsed", on);
  }
  $("toggle-left").addEventListener("click", () => setLeftCollapsed(true));
  if ($("toggle-left-bar")) $("toggle-left-bar").addEventListener("click", () => setLeftCollapsed(false));
  if ($("toggle-right")) $("toggle-right").addEventListener("click", () => setRightCollapsed(true));
  if ($("toggle-right-bar")) $("toggle-right-bar").addEventListener("click", () => setRightCollapsed(false));

  $("stop-server").addEventListener("click", async () => {
    try {
      await api("/api/stop", { method: "POST" });
      pushMessage(t("stopping"));
    } catch (err) {
      pushMessage(String(err.message || err));
    }
  });

  $("token-form").addEventListener("submit", (ev) => {
    ev.preventDefault();
    sessionStorage.setItem(tokenKey, $("token-input").value.trim());
    $("auth-error").hidden = true;
    $("auth-card").hidden = true;
    boot();
  });

  $("add-source").addEventListener("click", () => addSourceRow());
  $("check-setup").addEventListener("click", async () => {
    try {
      const data = await api("/api/setup/check", { method: "POST", body: JSON.stringify(formPayload()) });
      $("setup-msg").textContent = t("paths_ok") + " " + data.config.config_path;
      $("setup-msg").className = "ok";
      pushMessage(t("paths_ok"));
    } catch (err) {
      $("setup-msg").textContent = String(err.message || err);
      $("setup-msg").className = "error";
    }
  });
  $("save-setup").addEventListener("click", async () => {
    try {
      await api("/api/setup", { method: "POST", body: JSON.stringify(formPayload()) });
      pushMessage(t("saved"));
      await afterConfigured();
    } catch (err) {
      $("setup-msg").textContent = String(err.message || err);
      $("setup-msg").className = "error";
    }
  });
  $("load-config").addEventListener("click", async () => {
    try {
      await api("/api/setup/load", { method: "POST", body: JSON.stringify({ path: $("load-path").value.trim() }) });
      pushMessage(t("opened"));
      await afterConfigured();
    } catch (err) {
      $("setup-msg").textContent = String(err.message || err);
      $("setup-msg").className = "error";
    }
  });
  $("change-setup").addEventListener("click", async () => {
    setSetupMode(true);
    await fillSetupDefaults();
  });
  if ($("edit-dest")) {
    $("edit-dest").addEventListener("click", () => {
      $("dest-form").hidden = !$("dest-form").hidden;
    });
  }
  if ($("dest-form")) {
    $("dest-form").addEventListener("submit", async (ev) => {
      ev.preventDefault();
      try {
        await api("/api/paths", { method: "POST", body: JSON.stringify({ library_root: $("dest-input").value.trim() }) });
        pushMessage(t("dest_saved"));
        $("dest-form").hidden = true;
        await loadOverview();
      } catch (err) {
        pushMessage(String(err.message || err));
      }
    });
  }
  if ($("edit-quarantine")) {
    $("edit-quarantine").addEventListener("click", () => {
      $("quarantine-form").hidden = !$("quarantine-form").hidden;
    });
  }
  if ($("quarantine-form")) {
    $("quarantine-form").addEventListener("submit", async (ev) => {
      ev.preventDefault();
      if (!window.confirm(t("quarantine_warn"))) return;
      try {
        await api("/api/paths", { method: "POST", body: JSON.stringify({ quarantine: $("quarantine-input").value.trim() }) });
        pushMessage(t("quarantine_saved"));
        $("quarantine-form").hidden = true;
        await loadOverview();
      } catch (err) {
        pushMessage(String(err.message || err));
      }
    });
  }
  if ($("add-source-inline")) {
    $("add-source-inline").addEventListener("click", () => {
      $("add-source-form").hidden = !$("add-source-form").hidden;
    });
  }
  if ($("add-source-form")) {
    $("add-source-form").addEventListener("submit", async (ev) => {
      ev.preventDefault();
      const path = $("add-source-path").value.trim();
      if (!path) return;
      try {
        const cfg = await api("/api/config");
        const sources = (cfg.sources || []).map((s) => ({
          path: s.path,
          layout: s.layout,
          priority: s.priority,
        }));
        sources.push({ path, layout: "keep_relpath", priority: 5 });
        await api("/api/paths", { method: "POST", body: JSON.stringify({ sources }) });
        pushMessage(t("source_added"));
        $("add-source-form").hidden = true;
        $("add-source-path").value = "";
        await loadOverview();
      } catch (err) {
        pushMessage(String(err.message || err));
      }
    });
  }
  $("deep-scan").addEventListener("click", async () => {
    try {
      await runJob("deep-scan");
    } catch (err) {
      pushMessage(String(err.message || err));
    }
  });
  document.querySelectorAll("[data-job]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      try {
        await runJob(btn.getAttribute("data-job"));
      } catch (err) {
        pushMessage(String(err.message || err));
      }
    });
  });
  $("apply-btn").addEventListener("click", async () => {
    if (!$("apply-confirm").checked) {
      pushMessage(t("confirm_needed"));
      return;
    }
    try {
      await runJob("apply", { confirm: true });
    } catch (err) {
      pushMessage(String(err.message || err));
    }
  });

  window.addEventListener("resize", () => {
    if (overview.sources && overview.sources.length) renderDuplicates();
  });

  async function afterConfigured() {
    setSetupMode(false);
    await loadOverview();
    await refreshStatus();
  }

  async function boot() {
    setTheme(localStorage.getItem(themeKey) || "night");
    setLang(localStorage.getItem(langKey) || "en");
    applyI18n();
    $("bound-url").textContent = location.origin;
    if ($("bar-url")) $("bar-url").textContent = location.origin;
    if (!token()) {
      $("auth-card").hidden = false;
      return;
    }
    try {
      const health = await api("/api/health");
      $("auth-card").hidden = true;
      $("version").textContent = "v" + (health.version || "");
      await fillSetupDefaults();
      if (health.needs_setup) {
        setSetupMode(true);
        return;
      }
      await afterConfigured();
    } catch (err) {
      $("auth-card").hidden = false;
      $("auth-error").hidden = false;
      $("auth-error").textContent = err.message;
    }
  }

  boot();
  setInterval(refreshStatus, 2000);
})();
