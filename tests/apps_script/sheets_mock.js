// In-memory stand-in for the Google Apps Script services used by google_apps_script.js
const fs = require("fs");
const vm = require("vm");

class Range {
  constructor(sh, r, c, nr, nc) { Object.assign(this, { sh, r, c, nr: nr || 1, nc: nc || 1 }); }
  getValues() {
    this.sh.ss.cellsRead = (this.sh.ss.cellsRead || 0) + this.nr * this.nc;
    const out = [];
    for (let i = 0; i < this.nr; i++) {
      const row = this.sh.rows[this.r - 1 + i] || [];
      const o = [];
      for (let j = 0; j < this.nc; j++) o.push(row[this.c - 1 + j] === undefined ? "" : row[this.c - 1 + j]);
      out.push(o);
    }
    return out;
  }
  setValues(v) {
    for (let i = 0; i < this.nr; i++) {
      const idx = this.r - 1 + i;
      while (this.sh.rows.length <= idx) this.sh.rows.push([]);
      for (let j = 0; j < this.nc; j++) this.sh.rows[idx][this.c - 1 + j] = v[i][j];
    }
    return this;
  }
  setValue(x) { return this.setValues([[x]]); }
  getValue() { return this.getValues()[0][0]; }
  setFontWeight() { return this; }
  setBackground() { return this; }
  setFontColor() { return this; }
  sort() {
    const body = this.sh.rows.splice(this.r - 1, this.nr);
    const k = (row) => String(row[0]).replace(/ (\d):/, " 0$1:");
    body.sort((a, b) => (k(a) < k(b) ? -1 : k(a) > k(b) ? 1 : 0));
    this.sh.rows.splice(this.r - 1, 0, ...body);
  }
}

class Sheet {
  constructor(ss, name, rows) { this.ss = ss; this.name = name; this.rows = rows || []; }
  getName() { return this.name; }
  getParent() { return this.ss; }
  getLastRow() { return this.rows.length; }
  getLastColumn() { return this.rows.length ? Math.max(...this.rows.map((r) => r.length)) : 0; }
  getRange(r, c, nr, nc) { return new Range(this, r, c, nr, nc); }
  appendRow(row) { this.rows.push(row.slice()); }
  insertRowBefore(r) { this.rows.splice(r - 1, 0, []); }
  deleteRow(r) { this.rows.splice(r - 1, 1); }
  deleteRows(r, n) { this.rows.splice(r - 1, n); }
  setFrozenRows() {}
}

// Loads the script into a sandbox; returns { ctx, ss, logs, mails, props }
function loadScript(scriptPath) {
  const ss = {
    sheets: [],
    getSheets() { return this.sheets; },
    getSheetByName(n) { return this.sheets.find((s) => s.name === n) || null; },
    insertSheet(n, i) { const s = new Sheet(this, n); this.sheets.splice(i, 0, s); return s; },
    getSpreadsheetTimeZone() { return "America/Santiago"; },
    getId() { return "id"; },
    getUrl() { return "https://docs.google.com/spreadsheets/d/fake"; },
  };
  const logs = [];
  const mails = [];
  const props = { LAST_RECORDED_YEAR: "2026" };
  const lock = { free: true };
  const ctx = {
    SpreadsheetApp: { getActiveSpreadsheet: () => ss },
    LockService: {
      getScriptLock: () => ({ waitLock() {}, tryLock() { return lock.free; }, releaseLock() {} }),
    },
    PropertiesService: {
      getScriptProperties: () => ({
        getProperty: (k) => props[k] || null,
        setProperty: (k, v) => { props[k] = v; },
        deleteProperty: (k) => { delete props[k]; },
      }),
    },
    ContentService: { createTextOutput: (m) => ({ m, setMimeType() { return this; } }), MimeType: { TEXT: "t" } },
    Utilities: {
      formatDate(d, tz, fmt) {
        if (tz !== "UTC") return ctx.FAKE_NOW;   // only used for "now" in revisarEstacion
        return d.toISOString().replace("T", " ").substring(0, 19);
      },
    },
    FAKE_NOW: null,
    MailApp: { sendEmail: (to, subject, body) => mails.push({ to, subject, body }) },
    Session: { getEffectiveUser: () => ({ getEmail: () => "yo@example.com" }) },
    ScriptApp: {
      triggers: [],
      getProjectTriggers() { return this.triggers; },
      deleteTrigger(t) { this.triggers = this.triggers.filter((x) => x !== t); },
      newTrigger(fn) {
        const self = this;
        return { timeBased: () => ({ everyMinutes: (m) => ({ create: () => self.triggers.push({ fn, m, getHandlerFunction: () => fn }) }) }) };
      },
    },
    UrlFetchApp: {
      requests: [], code: 200, fail: false,
      fetch(url, opts) {
        if (this.fail) throw new Error("DNS error");
        this.requests.push({ url, opts });
        const code = this.code;
        return { getResponseCode: () => code, getContentText: () => (code === 200 ? "OK" : "Bad request") };
      },
    },
    Logger: { log: (m) => logs.push(String(m)) },
    DriveApp: {},
    console,
  };
  vm.createContext(ctx);
  vm.runInContext(fs.readFileSync(scriptPath, "utf8"), ctx);
  return { ctx, ss, logs, mails, props, lock, Sheet };
}

module.exports = { loadScript, Sheet };
