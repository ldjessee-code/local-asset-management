/* SPDX-License-Identifier: AGPL-3.0-or-later */
window.LAM_I18N = {
  defaultLang: "en",
  catalogs: {
    en: {
      skip_to_controls: "Skip to controls",
      app_name: "Local Asset Management",
      version: "Version",
      token: "Token",
      token_help: "From the terminal Token: line, or the UI: link.",
      save_token: "Save token",
      last_scan: "Last scan",
      never_scanned: "Not scanned yet",
      scan_stale: "This listing may be out of date. Scan again to refresh.",
      messages: "Messages",
      job_idle: "idle",
      job_running: "running",
      job_done: "done",
      job_error: "error",
      stop: "Stop",
      theme: "Color scheme",
      theme_night: "Night",
      theme_day: "Day",
      theme_deuteranopia: "Blue-green color blind",
      theme_protanopia: "Red color blind",
      theme_achromatopsia: "Gray (no color)",
      theme_high_contrast: "High contrast",
      language: "Language",
      collapse_session: "Hide session pane",
      expand_session: "Show session pane",
      safety: "Scan is read-only. Apply copies only after you confirm. Sources are not modified.",
      setup_title: "Set up this library",
      setup_lede: "Save library.jsonc outside sources and the destination. Then add folders.",
      open_config: "Open an existing file",
      create_config: "Or create one",
      save_config_as: "Save config as",
      output_folder: "Output folder",
      source_folders: "Source folders",
      add_source: "Add source",
      remove: "Remove",
      source_path: "Source folder",
      layout: "Layout",
      priority: "Priority",
      check_paths: "Check paths",
      save_and_use: "Save and use",
      change_setup: "Plan settings",
      plan_settings: "Plan settings",
      quarantine_warn: "Changing quarantine does not move files already copied there. Continue?",
      add_source_plus: "Add a source folder",
      edit_destination: "Change destination folder",
      edit_quarantine: "Change quarantine folder",
      dest_saved: "Destination updated.",
      quarantine_saved: "Quarantine updated.",
      source_added: "Source added.",
      messages_placeholder: "This listing may be out of date. Scan again to refresh.",
      welcome: "Ready. Scan to inventory sources.",
      dup_meta_diff: "Same name, date, and size — different contents",
      collapse_library: "Hide library pane",
      expand_library: "Show library pane",
      save: "Save",
      scan: "Scan",
      deep_scan: "Deep scan",
      plan: "Plan",
      apply: "Apply",
      report: "Report",
      confirm_apply: "I reviewed the duplicates and want to copy files",
      confirm_needed: "Tick the confirm box before Apply.",
      duplicates: "Duplicates",
      relative_path: "Relative path",
      no_duplicates: "No duplicates at this source",
      unique: "Only here",
      dup_meta: "Same name, date, and size",
      dup_hash: "Same file contents",
      in_library: "Already in library",
      empty_cell: "Not in this source",
      zip_contents: "Zip contents",
      space_to_save: "Space you could save",
      sources: "Sources",
      destination: "Destination",
      quarantine: "Quarantine",
      files: "files",
      browse: "Browse",
      up: "Up",
      missing_folder: "Folder not created yet",
      zip_pairing: "Zip pairing",
      zip_paired: "zip and folder both present",
      zip_only: "zip only",
      license: "License",
      source_code: "Source",
      unauthorized: "Unauthorized — use the Token: line in the terminal, or the UI: link.",
      stopping: "Stopping the engine. You can close this tab.",
      scan_finished: "Scan finished.",
      deep_finished: "Deep scan finished.",
      deep_running: "Deep scan (content hash) running in the background.",
      needs_deep: "Same name, date, and size found. Starting a deep scan for exact contents.",
      paths_ok: "Paths look good.",
      saved: "Saved. Scan is ready.",
      opened: "Opened config.",
      legend: "Legend",
      save: "Save",
    },
  },
};

window.t = function t(key, vars) {
  const lang = document.documentElement.lang || "en";
  const catalogs = window.LAM_I18N.catalogs;
  let s = (catalogs[lang] && catalogs[lang][key]) || catalogs.en[key] || key;
  if (vars) {
    Object.keys(vars).forEach((k) => {
      s = s.replaceAll("{" + k + "}", String(vars[k]));
    });
  }
  return s;
};

window.applyI18n = function applyI18n(root) {
  (root || document).querySelectorAll("[data-i18n]").forEach((el) => {
    el.textContent = window.t(el.getAttribute("data-i18n"));
  });
  (root || document).querySelectorAll("[data-i18n-aria]").forEach((el) => {
    el.setAttribute("aria-label", window.t(el.getAttribute("data-i18n-aria")));
  });
  (root || document).querySelectorAll("[data-i18n-title]").forEach((el) => {
    el.setAttribute("title", window.t(el.getAttribute("data-i18n-title")));
  });
  (root || document).querySelectorAll("[data-i18n-placeholder]").forEach((el) => {
    el.setAttribute("placeholder", window.t(el.getAttribute("data-i18n-placeholder")));
  });
};
