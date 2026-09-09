// ---- Config from the server (rendered into <body>) ----
const BODY = document.body;
const PEOPLE = JSON.parse(BODY.dataset.people);
const GOAL = parseInt(BODY.dataset.goal, 10) || 3;
const TODAY = BODY.dataset.today;                // "YYYY-MM-DD"
const PERSON_BY_KEY = Object.fromEntries(PEOPLE.map((p) => [p.key, p]));

// ---- State ----
let workouts = {};           // "person|day" -> notes (string)
let activePerson = PEOPLE[0].key;
let viewYear, viewMonth;     // month currently shown (month = 0-11)

// ---- Date helpers (all local, ISO strings) ----
const pad = (n) => String(n).padStart(2, "0");
const iso = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
const parseIso = (s) => { const [y, m, d] = s.split("-").map(Number); return new Date(y, m - 1, d); };

// Monday-based day index (Mon=0 ... Sun=6)
const dowMon = (d) => (d.getDay() + 6) % 7;

function mondayOf(d) {
  const m = new Date(d);
  m.setDate(m.getDate() - dowMon(m));
  m.setHours(0, 0, 0, 0);
  return m;
}
function weekKey(d) { return iso(mondayOf(d)); }

const MONTHS = ["January","February","March","April","May","June","July","August","September","October","November","December"];

// ---- API ----
async function loadWorkouts() {
  const res = await fetch("/api/workouts");
  const rows = await res.json();
  workouts = {};
  for (const r of rows) workouts[`${r.person}|${r.day}`] = r.notes || "";
}
async function apiToggle(person, day) {
  const res = await fetch("/api/toggle", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ person, day }),
  });
  return res.json();
}
async function apiNote(person, day, notes) {
  await fetch("/api/note", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ person, day, notes }),
  });
}

const isOn = (person, day) => Object.prototype.hasOwnProperty.call(workouts, `${person}|${day}`);
const noteOf = (person, day) => workouts[`${person}|${day}`] || "";

// Toggle locally + on server, then re-render everything.
async function toggle(person, day) {
  const r = await apiToggle(person, day);
  const k = `${person}|${day}`;
  if (r.active) { if (!(k in workouts)) workouts[k] = ""; }
  else { delete workouts[k]; }
  renderAll();
}

// ---- Week counts ----
function countInWeek(person, mondayIso) {
  const start = parseIso(mondayIso);
  let n = 0;
  for (let i = 0; i < 7; i++) {
    const d = new Date(start); d.setDate(start.getDate() + i);
    if (isOn(person, iso(d))) n++;
  }
  return n;
}

// Consecutive weeks (ending at current week) that met the goal.
// An incomplete current week that hasn't hit goal yet doesn't break the streak.
function weekStreak(person) {
  const wk = mondayOf(parseIso(TODAY));
  let streak = 0;
  for (let i = 0; i < 260; i++) {   // look back up to ~5 years
    const c = countInWeek(person, iso(wk));
    if (c >= GOAL) {
      streak++;
    } else if (i > 0) {
      break;   // a completed past week missed the goal — streak ends
    }
    // i === 0 and current week not yet at goal: don't count, don't break
    wk.setDate(wk.getDate() - 7);
  }
  return streak;
}

function monthCount(person, year, month) {
  let n = 0;
  for (const k in workouts) {
    const [p, day] = k.split("|");
    if (p !== person) continue;
    const d = parseIso(day);
    if (d.getFullYear() === year && d.getMonth() === month) n++;
  }
  return n;
}
function totalCount(person) {
  return Object.keys(workouts).filter((k) => k.startsWith(person + "|")).length;
}

// ---- Renderers ----
function renderWho() {
  document.querySelectorAll(".who-chip").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.person === activePerson);
  });
}

function ring(pct, color, label) {
  const R = 26, C = 2 * Math.PI * R;
  const off = C * (1 - Math.min(pct, 1));
  return `<svg class="wc-ring" width="64" height="64" viewBox="0 0 64 64">
    <circle cx="32" cy="32" r="${R}" fill="none" stroke="rgba(255,255,255,0.1)" stroke-width="7"/>
    <circle cx="32" cy="32" r="${R}" fill="none" stroke="${color}" stroke-width="7"
      stroke-linecap="round" stroke-dasharray="${C}" stroke-dashoffset="${off}"
      transform="rotate(-90 32 32)" style="transition:stroke-dashoffset .5s ease"/>
    <text x="32" y="37" text-anchor="middle" font-family="Space Grotesk" font-size="16"
      font-weight="700" fill="#eef2ff">${label}</text>
  </svg>`;
}

function renderWeek() {
  const curMon = mondayOf(parseIso(TODAY));
  const end = new Date(curMon); end.setDate(curMon.getDate() + 6);
  document.getElementById("weekRange").textContent =
    `${MONTHS[curMon.getMonth()].slice(0,3)} ${curMon.getDate()} – ${MONTHS[end.getMonth()].slice(0,3)} ${end.getDate()}`;

  const wrap = document.getElementById("weekCards");
  wrap.innerHTML = PEOPLE.map((p) => {
    const c = countInWeek(p.key, iso(curMon));
    const hit = c >= GOAL;
    const dots = Array.from({ length: GOAL }, (_, i) =>
      `<i class="${i < c ? "on" : ""}"></i>`).join("");
    const extra = c > GOAL ? ` +${c - GOAL} bonus` : "";
    return `<div class="wc" style="--pc:${p.color}">
      ${ring(c / GOAL, p.color, `${c}/${GOAL}`)}
      <div class="wc-info">
        <div class="wc-name">${p.name}
          <span class="tag ${hit ? "wc-tag-hit" : "wc-tag-go"}">${hit ? "GOAL HIT ✓" : `${GOAL - c} to go`}</span>
        </div>
        <div class="wc-sub">${c} session${c === 1 ? "" : "s"} this week${extra}</div>
        <div class="wc-dots">${dots}</div>
      </div>
    </div>`;
  }).join("");
}

function renderCalendar() {
  document.getElementById("monthLabel").textContent = `${MONTHS[viewMonth]} ${viewYear}`;
  const grid = document.getElementById("calGrid");
  const first = new Date(viewYear, viewMonth, 1);
  const startPad = dowMon(first);
  const gridStart = new Date(first); gridStart.setDate(1 - startPad);
  const todayD = parseIso(TODAY);

  let html = "";
  for (let i = 0; i < 42; i++) {
    const d = new Date(gridStart); d.setDate(gridStart.getDate() + i);
    const dayIso = iso(d);
    const out = d.getMonth() !== viewMonth;
    const isToday = dayIso === TODAY;
    const future = d > todayD;
    const cls = ["day", out && "out", isToday && "today", future && !isToday && "future"].filter(Boolean).join(" ");

    const pills = PEOPLE.map((p) => {
      const on = isOn(p.key, dayIso);
      const hasNote = on && noteOf(p.key, dayIso).trim();
      return `<button class="pill ${on ? "on" : ""} ${hasNote ? "has-note" : ""}"
        style="--pc:${p.color}" data-person="${p.key}" data-day="${dayIso}"
        title="${p.name}">${p.name[0]}</button>`;
    }).join("");

    html += `<div class="${cls}" data-day="${dayIso}">
      <span class="day-num">${d.getDate()}</span>
      <div class="day-pills">${pills}</div>
    </div>`;
  }
  grid.innerHTML = html;
}

function renderStats() {
  const col = document.getElementById("statsCol");
  const streakRows = PEOPLE.map((p) => {
    const s = weekStreak(p.key);
    return `<div class="stat-row" style="--pc:${p.color}">
      <span class="stat-who"><span class="dot"></span>${p.name}</span>
      <span class="stat-val">${s}<small> wk</small> ${s > 0 ? '<span class="streak-flame">🔥</span>' : ""}</span>
    </div>`;
  }).join("");

  const monthRows = PEOPLE.map((p) => {
    const c = monthCount(p.key, viewYear, viewMonth);
    return `<div class="stat-row" style="--pc:${p.color}">
      <span class="stat-who"><span class="dot"></span>${p.name}</span>
      <span class="stat-val">${c}<small> days</small></span>
    </div>`;
  }).join("");

  const totalRows = PEOPLE.map((p) => {
    const c = totalCount(p.key);
    return `<div class="stat-row" style="--pc:${p.color}">
      <span class="stat-who"><span class="dot"></span>${p.name}</span>
      <span class="stat-val">${c}<small> total</small></span>
    </div>`;
  }).join("");

  col.innerHTML = `
    <div class="stat-card"><h3>Week streak</h3>${streakRows}</div>
    <div class="stat-card"><h3>${MONTHS[viewMonth]} sessions</h3>${monthRows}</div>
    <div class="stat-card"><h3>All time</h3>${totalRows}</div>`;
}

function renderAll() {
  renderWho();
  renderWeek();
  renderCalendar();
  renderStats();
}

// ---- Day detail sheet ----
const sheet = document.getElementById("sheet");
const backdrop = document.getElementById("sheetBackdrop");

function openSheet(dayIso) {
  const d = parseIso(dayIso);
  document.getElementById("sheetDate").textContent =
    d.toLocaleDateString(undefined, { weekday: "long", month: "long", day: "numeric" });

  document.getElementById("sheetBody").innerHTML = PEOPLE.map((p) => {
    const on = isOn(p.key, dayIso);
    return `<div class="sheet-person" style="--pc:${p.color}">
      <div class="sp-head">
        <span class="sp-name"><span class="dot"></span>${p.name}</span>
        <button class="sp-toggle ${on ? "on" : ""}" data-person="${p.key}" data-day="${dayIso}">
          ${on ? "Worked out ✓" : "Mark workout"}
        </button>
      </div>
      <textarea class="sp-note" placeholder="What did ${p.name} train? (legs, push, run…)"
        data-person="${p.key}" data-day="${dayIso}">${noteOf(p.key, dayIso)}</textarea>
    </div>`;
  }).join("");

  sheet.hidden = false; backdrop.hidden = false;
}
function closeSheet() { sheet.hidden = true; backdrop.hidden = true; }

// ---- Events ----
document.getElementById("who").addEventListener("click", (e) => {
  const chip = e.target.closest(".who-chip");
  if (!chip) return;
  activePerson = chip.dataset.person;
  renderWho();
});

document.getElementById("calGrid").addEventListener("click", (e) => {
  const pill = e.target.closest(".pill");
  if (pill) {
    e.stopPropagation();
    toggle(pill.dataset.person, pill.dataset.day);
    return;
  }
  const cell = e.target.closest(".day");
  if (cell) openSheet(cell.dataset.day);
});

document.getElementById("sheetBody").addEventListener("click", (e) => {
  const btn = e.target.closest(".sp-toggle");
  if (!btn) return;
  toggle(btn.dataset.person, btn.dataset.day).then(() => openSheet(btn.dataset.day));
});
document.getElementById("sheetBody").addEventListener("change", (e) => {
  const ta = e.target.closest(".sp-note");
  if (!ta) return;
  const val = ta.value.trim();
  const k = `${ta.dataset.person}|${ta.dataset.day}`;
  if (val && !(k in workouts)) workouts[k] = val;      // note implies a workout
  else if (k in workouts) workouts[k] = val;
  apiNote(ta.dataset.person, ta.dataset.day, val).then(renderAll);
});

document.getElementById("sheetClose").addEventListener("click", closeSheet);
backdrop.addEventListener("click", closeSheet);
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeSheet(); });

document.getElementById("prevMonth").addEventListener("click", () => {
  viewMonth--; if (viewMonth < 0) { viewMonth = 11; viewYear--; } renderCalendar(); renderStats();
});
document.getElementById("nextMonth").addEventListener("click", () => {
  viewMonth++; if (viewMonth > 11) { viewMonth = 0; viewYear++; } renderCalendar(); renderStats();
});
document.getElementById("todayBtn").addEventListener("click", () => {
  const t = parseIso(TODAY); viewYear = t.getFullYear(); viewMonth = t.getMonth();
  renderCalendar(); renderStats();
});

// ---- Init ----
(async function init() {
  const t = parseIso(TODAY);
  viewYear = t.getFullYear();
  viewMonth = t.getMonth();
  await loadWorkouts();
  renderAll();
})();
