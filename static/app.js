// ---- Config from the server (rendered into <body>) ----
const BODY = document.body;
const PEOPLE = JSON.parse(BODY.dataset.people);
const ME = JSON.parse(BODY.dataset.me);          // the logged-in user
const GOAL = parseInt(BODY.dataset.goal, 10) || 3;
// "Today" is the viewer's LOCAL date, not the server's (Render runs on UTC, which
// rolls over hours ahead of the Americas). Computed self-contained since the date
// helpers below aren't defined yet.
const TODAY = (() => {
  const d = new Date();
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
})();
const PERSON_BY_KEY = Object.fromEntries(PEOPLE.map((p) => [p.key, p]));

// ---- State ----
let workouts = {};           // "person|day" -> notes (string)
// You can only log yourself; the active person is always the logged-in user.
const activePerson = ME.key;
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
// Celebrates when the toggle pushes that person's current week onto the goal.
async function toggle(person, day) {
  const curMon = iso(mondayOf(parseIso(TODAY)));
  const before = countInWeek(person, curMon);

  const r = await apiToggle(person, day);
  const k = `${person}|${day}`;
  if (r.active) { if (!(k in workouts)) workouts[k] = ""; }
  else { delete workouts[k]; }

  const after = countInWeek(person, curMon);
  if (after === GOAL && after > before) fireConfetti();

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
    const wentToday = isOn(p.key, TODAY);
    return `<div class="wc" style="--pc:${p.color}">
      ${ring(c / GOAL, p.color, `${c}/${GOAL}`)}
      <div class="wc-info">
        <div class="wc-name">${p.name}
          <span class="tag ${hit ? "wc-tag-hit" : "wc-tag-go"}">${hit ? "GOAL HIT ✓" : `${GOAL - c} to go`}</span>
        </div>
        <div class="wc-sub">${c} session${c === 1 ? "" : "s"} this week${extra}</div>
        <div class="wc-dots">${dots}</div>
        ${p.key === ME.key ? `<button class="wc-log ${wentToday ? "done" : ""}" data-person="${p.key}" data-day="${TODAY}">
          ${wentToday ? "Logged today ✓" : "＋ Log today"}
        </button>` : ""}
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
      const mine = p.key === ME.key;
      // Only your own pills are clickable; everyone else's are read-only.
      return `<button class="pill ${on ? "on" : ""} ${hasNote ? "has-note" : ""} ${mine ? "" : "readonly"}"
        style="--pc:${p.color}" ${mine ? `data-person="${p.key}" data-day="${dayIso}"` : ""}
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
    <div class="stat-card"><h3>All time</h3>${totalRows}</div>
    <div class="stat-card"><h3>Achievements</h3>${renderBadges()}</div>`;
}

// ---- Extra analytics (all derived from the workouts list) ----
function allDays(person) {
  return Object.keys(workouts)
    .filter((k) => k.startsWith(person + "|"))
    .map((k) => k.split("|")[1])
    .sort();
}

// Longest run of consecutive calendar days worked out.
function longestDayStreak(person) {
  const days = allDays(person);
  if (!days.length) return 0;
  let best = 1, run = 1;
  for (let i = 1; i < days.length; i++) {
    const prev = parseIso(days[i - 1]);
    const cur = parseIso(days[i]);
    const gap = Math.round((cur - prev) / 86400000);
    run = gap === 1 ? run + 1 : 1;
    best = Math.max(best, run);
  }
  return best;
}

// Most sessions in any single week.
function bestWeek(person) {
  const seen = {};
  for (const d of allDays(person)) seen[weekKey(parseIso(d))] = (seen[weekKey(parseIso(d))] || 0) + 1;
  return Object.values(seen).reduce((m, v) => Math.max(m, v), 0);
}
function hitGoalEver(person) { return bestWeek(person) >= GOAL; }

// Earliest logged Monday across both people (for weekly comparisons).
function firstMonday() {
  const all = Object.keys(workouts).map((k) => k.split("|")[1]).sort();
  return all.length ? mondayOf(parseIso(all[0])) : mondayOf(parseIso(TODAY));
}

// Weeks won head-to-head + weeks both hit the goal ("team weeks").
function weekTallies() {
  if (PEOPLE.length !== 2) return null;
  const [a, b] = PEOPLE;
  const res = { [a.key]: 0, [b.key]: 0, team: 0 };
  const wk = firstMonday();
  const cur = mondayOf(parseIso(TODAY));
  while (wk <= cur) {
    const ca = countInWeek(a.key, iso(wk));
    const cb = countInWeek(b.key, iso(wk));
    if (ca > cb) res[a.key]++;
    else if (cb > ca) res[b.key]++;
    if (ca >= GOAL && cb >= GOAL) res.team++;
    wk.setDate(wk.getDate() + 7);
  }
  return res;
}

// ---- Hype / trash-talk banner ----
const pick = (arr) => arr[Math.floor(Math.random() * arr.length)];

function renderHype() {
  const el = document.getElementById("hype");
  if (!el) return;
  const total = Object.keys(workouts).length;
  let spark = "💪", msg;

  if (total === 0) {
    spark = "🌱";
    msg = "Empty board. Whoever logs the first workout owns the bragging rights.";
  } else if (PEOPLE.length === 2) {
    const [a, b] = PEOPLE;
    const mon = iso(mondayOf(parseIso(TODAY)));
    const ca = countInWeek(a.key, mon), cb = countInWeek(b.key, mon);
    const aHit = ca >= GOAL, bHit = cb >= GOAL;
    if (aHit && bHit) { spark = "🔥"; msg = pick([
      `Both of you hit ${GOAL}× this week. Certified gym rats.`,
      `${a.name} and ${b.name} both cleared the goal. Elite week.`]); }
    else if (aHit || bHit) {
      const win = aHit ? a : b, lose = aHit ? b : a, need = GOAL - (aHit ? cb : ca);
      spark = "⏰"; msg = pick([
        `${win.name} hit the goal. ${lose.name}'s got ${need} to go — tick tock.`,
        `${win.name}'s done for the week. ${lose.name}, you gonna let that slide?`]);
    } else if (ca === cb) {
      spark = ca === 0 ? "👀" : "⚔️";
      msg = ca === 0 ? "Fresh week, nobody's moved yet. Who goes first?"
        : `Dead even at ${ca} each this week. Somebody break the tie.`;
    } else {
      const win = ca > cb ? a : b, diff = Math.abs(ca - cb);
      spark = "📈"; msg = `${win.name} is up ${diff} this week. Don't get comfortable.`;
    }
  } else if (PEOPLE.length === 1) {
    const mon = iso(mondayOf(parseIso(TODAY)));
    const c = countInWeek(ME.key, mon);
    spark = c >= GOAL ? "🔥" : "💪";
    msg = c >= GOAL ? `${c}× this week — goal crushed. Invite a friend to race.`
      : `${c}/${GOAL} this week. Send the link to a friend and make it a competition.`;
  } else {
    // 3+ people: who's leading this week?
    const mon = iso(mondayOf(parseIso(TODAY)));
    const ranked = PEOPLE.map((p) => ({ p, c: countInWeek(p.key, mon) })).sort((x, y) => y.c - x.c);
    const top = ranked[0];
    if (top.c === 0) { spark = "👀"; msg = "Fresh week, nobody's logged yet. Who moves first?"; }
    else if (ranked[1] && ranked[1].c === top.c) { spark = "⚔️"; msg = `It's a ${top.c}-way tie at the top this week. Break it.`; }
    else { spark = "👑"; msg = `${top.p.name} leads the week with ${top.c}. Everyone else is chasing.`; }
  }
  el.innerHTML = `<span class="spark">${spark}</span><span>${msg}</span>`;
}

// ---- Leaderboard (3+ people) ----
function renderLeaderboard(el) {
  const ranked = PEOPLE.map((p) => ({ p, c: monthCount(p.key, viewYear, viewMonth) }))
    .sort((x, y) => y.c - x.c);
  const max = Math.max(1, ranked[0].c);
  const medals = ["🥇", "🥈", "🥉"];
  el.style.display = "";
  el.innerHTML = `
    <div class="h2h-head">
      <h2>🏆 Leaderboard</h2>
      <span class="sub">${MONTHS[viewMonth]} ${viewYear}</span>
    </div>
    <div class="lb">${ranked.map((r, i) => `
      <div class="lb-row ${r.p.key === ME.key ? "me" : ""}" style="--pc:${r.p.color}">
        <span class="lb-rank">${medals[i] || (i + 1)}</span>
        <span class="lb-name"><span class="dot"></span>${r.p.name}</span>
        <span class="lb-bar"><i style="width:${(r.c / max) * 100}%"></i></span>
        <span class="lb-n">${r.c}</span>
      </div>`).join("")}</div>`;
}

// ---- Head-to-head arena ----
function renderHeadToHead() {
  const el = document.getElementById("h2h");
  if (!el) return;
  if (PEOPLE.length < 2) { el.style.display = "none"; return; }
  if (PEOPLE.length > 2) { return renderLeaderboard(el); }
  const [a, b] = PEOPLE;
  const ma = monthCount(a.key, viewYear, viewMonth);
  const mb = monthCount(b.key, viewYear, viewMonth);
  const t = weekTallies();

  const side = (p, val, lead) => `
    <div class="h2h-side ${lead ? "lead" : ""}" style="--pc:${p.color}">
      <div class="h2h-crown">👑</div>
      <div class="h2h-name"><span class="dot"></span>${p.name}</div>
      <div class="h2h-big">${val}</div>
      <div class="h2h-cap">sessions in ${MONTHS[viewMonth]}</div>
    </div>`;

  el.style.display = "";
  el.innerHTML = `
    <div class="h2h-head">
      <h2>⚔️ Head to head</h2>
      <span class="sub">${MONTHS[viewMonth]} ${viewYear}</span>
    </div>
    <div class="h2h-grid">
      ${side(a, ma, ma > mb)}
      <div class="h2h-vs">VS</div>
      ${side(b, mb, mb > ma)}
    </div>
    <div class="h2h-foot">
      <div class="h2h-stat"><div class="n" style="color:${a.color}">${t[a.key]}</div><div class="l">${a.name} weeks won</div></div>
      <div class="h2h-stat"><div class="n">🤝 ${t.team}</div><div class="l">team weeks</div></div>
      <div class="h2h-stat"><div class="n" style="color:${b.color}">${t[b.key]}</div><div class="l">${b.name} weeks won</div></div>
    </div>`;
}

// ---- Achievements ----
const ACHIEVEMENTS = [
  { ico: "🥇", name: "First Rep", desc: "Log your first workout", test: (p) => totalCount(p) >= 1 },
  { ico: "📅", name: "Consistent", desc: `Hit ${GOAL}× in a week`, test: (p) => hitGoalEver(p) },
  { ico: "🔟", name: "Double Digits", desc: "10 workouts total", test: (p) => totalCount(p) >= 10 },
  { ico: "⚡", name: "Perfect Week", desc: "5 sessions in one week", test: (p) => bestWeek(p) >= 5 },
  { ico: "🔥", name: "On Fire", desc: "3-week goal streak", test: (p) => weekStreak(p) >= 3 },
  { ico: "👑", name: "Unstoppable", desc: "6-week goal streak", test: (p) => weekStreak(p) >= 6 },
];

function renderBadges() {
  return `<div class="badges">${ACHIEVEMENTS.map((a) => {
    const earned = PEOPLE.map((p) => a.test(p.key));
    const anyone = earned.some(Boolean);
    const who = PEOPLE.map((p, i) =>
      `<i class="${earned[i] ? "on" : ""}" style="--wc:${p.color}" title="${p.name}"></i>`).join("");
    return `<div class="badge ${anyone ? "" : "locked"}">
      <div class="badge-ico">${a.ico}</div>
      <div class="badge-info"><div class="badge-name">${a.name}</div><div class="badge-desc">${a.desc}</div></div>
      <div class="badge-who">${who}</div>
    </div>`;
  }).join("")}</div>`;
}

// ---- Consistency heatmap (last ~53 weeks, GitHub-style) ----
function renderHeatmap() {
  const el = document.getElementById("heatCard");
  if (!el) return;
  const curMon = mondayOf(parseIso(TODAY));
  const start = new Date(curMon); start.setDate(start.getDate() - 52 * 7);

  let cells = "";
  for (let w = 0; w <= 52; w++) {
    for (let d = 0; d < 7; d++) {
      const day = new Date(start); day.setDate(start.getDate() + w * 7 + d);
      const dayIso = iso(day);
      const who = PEOPLE.filter((p) => isOn(p.key, dayIso));
      let style = "";
      if (who.length >= 2) {
        style = `background:linear-gradient(135deg, ${who[0].color}, ${who[who.length - 1].color})`;
      } else if (who.length === 1) {
        style = `background:${who[0].color}`;
      }
      const isToday = dayIso === TODAY;
      const names = who.map((p) => p.name).join(" & ") || "rest day";
      cells += `<div class="heat-cell ${isToday ? "today" : ""}" style="${style}" title="${dayIso}: ${names}"></div>`;
    }
  }

  const legendKey = (label, style) => `<span class="k" style="${style}"></span>${label}`;
  el.innerHTML = `
    <div class="heat-head">
      <h2>🗓️ Last year of workouts</h2>
      <div class="heat-legend">
        ${legendKey("none", "")}
        ${legendKey(PEOPLE[0].name, `background:${PEOPLE[0].color}`)}
        ${PEOPLE[1] ? legendKey(PEOPLE[1].name, `background:${PEOPLE[1].color}`) : ""}
        ${PEOPLE[1] ? legendKey("both", `background:linear-gradient(135deg,${PEOPLE[0].color},${PEOPLE[1].color})`) : ""}
      </div>
    </div>
    <div class="heat-scroll"><div class="heat-grid">${cells}</div></div>`;
}

// ---- Confetti (dependency-free) ----
function fireConfetti() {
  const canvas = document.getElementById("confetti");
  if (!canvas) return;
  const ctx = canvas.getContext("2d");
  const DPR = window.devicePixelRatio || 1;
  canvas.width = window.innerWidth * DPR;
  canvas.height = window.innerHeight * DPR;
  ctx.scale(DPR, DPR);
  const W = window.innerWidth, H = window.innerHeight;
  const colors = ["#38bdf8", "#fb923c", "#34d399", "#facc15", "#f472b6"];
  const parts = Array.from({ length: 140 }, () => ({
    x: W / 2 + (Math.random() - 0.5) * 120,
    y: H / 3,
    vx: (Math.random() - 0.5) * 12,
    vy: Math.random() * -12 - 4,
    size: Math.random() * 7 + 4,
    color: colors[(Math.random() * colors.length) | 0],
    rot: Math.random() * Math.PI,
    vr: (Math.random() - 0.5) * 0.3,
  }));
  const t0 = performance.now();
  (function frame(now) {
    const t = now - t0;
    ctx.clearRect(0, 0, W, H);
    parts.forEach((p) => {
      p.vy += 0.4;              // gravity
      p.x += p.vx; p.y += p.vy; p.rot += p.vr;
      ctx.save();
      ctx.translate(p.x, p.y);
      ctx.rotate(p.rot);
      ctx.globalAlpha = Math.max(0, 1 - t / 2200);
      ctx.fillStyle = p.color;
      ctx.fillRect(-p.size / 2, -p.size / 2, p.size, p.size * 0.6);
      ctx.restore();
    });
    if (t < 2200) requestAnimationFrame(frame);
    else ctx.clearRect(0, 0, W, H);
  })(t0);
}

function renderAll() {
  renderWho();
  renderHype();
  renderWeek();
  renderHeadToHead();
  renderCalendar();
  renderStats();
  renderHeatmap();
}

// ---- Day detail sheet ----
const sheet = document.getElementById("sheet");
const backdrop = document.getElementById("sheetBackdrop");

function openSheet(dayIso) {
  const d = parseIso(dayIso);
  document.getElementById("sheetDate").textContent =
    d.toLocaleDateString(undefined, { weekday: "long", month: "long", day: "numeric" });

  // Put the logged-in user first; only their row is editable.
  const ordered = [...PEOPLE].sort((a, b) => (a.key === ME.key ? -1 : b.key === ME.key ? 1 : 0));
  document.getElementById("sheetBody").innerHTML = ordered.map((p) => {
    const on = isOn(p.key, dayIso);
    const mine = p.key === ME.key;
    const note = noteOf(p.key, dayIso);
    if (!mine) {
      // Read-only view of someone else's day.
      return `<div class="sheet-person" style="--pc:${p.color}">
        <div class="sp-head">
          <span class="sp-name"><span class="dot"></span>${p.name}</span>
          <span class="sp-status ${on ? "on" : ""}">${on ? "Worked out ✓" : "Rest day"}</span>
        </div>
        ${on && note.trim() ? `<div class="sp-note-ro">${note.replace(/</g, "&lt;")}</div>` : ""}
      </div>`;
    }
    return `<div class="sheet-person" style="--pc:${p.color}">
      <div class="sp-head">
        <span class="sp-name"><span class="dot"></span>${p.name} <span class="sp-you">you</span></span>
        <button class="sp-toggle ${on ? "on" : ""}" data-person="${p.key}" data-day="${dayIso}">
          ${on ? "Worked out ✓" : "Mark workout"}
        </button>
      </div>
      <textarea class="sp-note" placeholder="What did you train? (legs, push, run…)"
        data-person="${p.key}" data-day="${dayIso}">${note}</textarea>
    </div>`;
  }).join("");

  sheet.hidden = false; backdrop.hidden = false;
}
function closeSheet() { sheet.hidden = true; backdrop.hidden = true; }

// ---- Events ----
document.getElementById("calGrid").addEventListener("click", (e) => {
  // Only your own (non-readonly) pills carry data-person; readonly pills fall
  // through to open the day sheet.
  const pill = e.target.closest(".pill:not(.readonly)");
  if (pill && pill.dataset.person) {
    e.stopPropagation();
    toggle(pill.dataset.person, pill.dataset.day);
    return;
  }
  const cell = e.target.closest(".day");
  if (cell) openSheet(cell.dataset.day);
});

// Quick "Log today" buttons on the week cards.
document.getElementById("weekCards").addEventListener("click", (e) => {
  const btn = e.target.closest(".wc-log");
  if (!btn) return;
  toggle(btn.dataset.person, btn.dataset.day);
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

// ==================== RANDOM PEEK ====================
// A small photo (static/jumpscare.jpg) quietly appears in a random corner every
// so often. Dismiss it with the X. Does nothing if the image is missing.
(function () {
  const el = document.getElementById("jumpscare");
  if (!el) return;
  const src = el.querySelector("img") ? el.querySelector("img").src : null;

  // Only arm once we know the image actually loads.
  let ready = false;
  if (src) {
    const probe = new Image();
    probe.onload = () => { ready = true; schedule(); };
    probe.onerror = () => { ready = false; };
    probe.src = src;
  }

  function hide() { el.hidden = true; }

  function fire() {
    const img = el.querySelector("img");
    if (!ready || !img || !img.complete || img.naturalWidth === 0) return;
    if (!el.hidden) return;   // already peeking — wait for dismiss

    // Drop it into a random corner with a small margin.
    const m = 20 + Math.round(Math.random() * 30);
    el.style.top = el.style.bottom = el.style.left = el.style.right = "auto";
    el.style[Math.random() < 0.5 ? "top" : "bottom"] = m + "px";
    el.style[Math.random() < 0.5 ? "left" : "right"] = m + "px";
    el.hidden = false;
  }

  function schedule() {
    const delay = 25000 + Math.random() * 50000;   // every ~25–75s
    setTimeout(() => { fire(); schedule(); }, delay);
  }

  const closeBtn = document.getElementById("peekClose");
  if (closeBtn) closeBtn.addEventListener("click", hide);
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") hide(); });

  // expose for manual testing in the console
  window.__jumpscare = fire;
})();
