// Hearthstone Chess replay viewer.
// Loads a stored game by slug, builds the initial board from the two
// 8-point setups, then steps through the event log. Stateless replay —
// we don't re-run the engine, just mutate a board map per event.

const PIECE_GLYPH = {
  king: "♚", queen: "♛", rook: "♜", bishop: "♝", knight: "♞", pawn: "♟",
};

const FILES = ["a", "b", "c", "d", "e", "f", "g", "h"];

function sqKey(file, rank) { return file + rank; }
function isLight(file, rank) {
  const f = FILES.indexOf(file);
  return (f + rank) % 2 === 1;
}

// ---- DOM helpers ----
const $ = (id) => document.getElementById(id);
const boardEl = $("board");
const eventsEl = $("events");
const readoutEl = $("step-readout");
const playBtn = $("btn-play");
const speedSel = $("speed");

function escapeHtml(s) {
  return (s ?? "").replace(/[&<>"']/g, c =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// ---- replay state ----
let game = null;
let cursor = 0;             // events applied: events[0..cursor-1]
let boardMap = new Map();   // sq → {color, kind}
let activeSeat = "white";
let lastMove = null;        // {from, to}
let playing = false;
let playTimer = null;

function buildInitialBoard() {
  boardMap = new Map();
  // Kings start on e1/e8 — the in-game starting board.
  boardMap.set("e1", { color: "white", kind: "king" });
  boardMap.set("e8", { color: "black", kind: "king" });
  for (const pk of game.white_setup || []) {
    boardMap.set(pk.square, { color: "white", kind: pk.kind });
  }
  for (const pk of game.black_setup || []) {
    boardMap.set(pk.square, { color: "black", kind: pk.kind });
  }
  activeSeat = "white";
  lastMove = null;
}

function applyEvent(ev) {
  switch (ev.kind) {
    case "piece_moved": {
      const piece = boardMap.get(ev.from);
      if (piece) {
        boardMap.delete(ev.from);
        // Overwrite anything at the destination (captures handled by the
        // adjacent piece_captured event too — idempotent).
        boardMap.set(ev.to, piece);
        lastMove = { from: ev.from, to: ev.to };
      }
      break;
    }
    case "piece_captured": {
      // Server emits this alongside piece_moved on a capture (sq == dst),
      // but piece_moved already overwrote the destination with the
      // attacker. Only delete when the piece on sq is actually the victim
      // — otherwise we'd wipe the attacker that just landed.
      const cur = boardMap.get(ev.sq);
      if (cur && cur.color === ev.victim_color && cur.kind === ev.victim_kind) {
        boardMap.delete(ev.sq);
      }
      break;
    }
    case "piece_placed":
      boardMap.set(ev.sq, { color: ev.color, kind: ev.piece_kind });
      break;
    case "piece_promoted": {
      const p = boardMap.get(ev.sq);
      if (p) p.kind = ev.to;
      break;
    }
    case "piece_converted": {
      const p = boardMap.get(ev.sq);
      if (p) p.color = ev.to_color;
      break;
    }
    case "turn_end":
      activeSeat = ev.next;
      lastMove = null;
      break;
    // card_played, card_drawn, card_burned, card_stolen, king_captured:
    // narration only — no board mutation needed.
  }
}

function rewindTo(target) {
  buildInitialBoard();
  cursor = 0;
  while (cursor < target) {
    applyEvent(game.event_log[cursor]);
    cursor++;
  }
}

function step(direction) {
  pause();
  if (direction > 0) {
    if (cursor >= game.event_log.length) return;
    applyEvent(game.event_log[cursor]);
    cursor++;
  } else {
    if (cursor === 0) return;
    // No reverse-event mechanism — rewind from start.
    rewindTo(cursor - 1);
  }
  render();
}

function jump(target) {
  pause();
  target = Math.max(0, Math.min(game.event_log.length, target));
  rewindTo(target);
  render();
}

function play() {
  if (cursor >= game.event_log.length) jump(0);
  playing = true;
  playBtn.textContent = "⏸";
  const tick = () => {
    if (!playing) return;
    if (cursor >= game.event_log.length) { pause(); return; }
    applyEvent(game.event_log[cursor]);
    cursor++;
    render();
    playTimer = setTimeout(tick, Number(speedSel.value));
  };
  tick();
}

function pause() {
  playing = false;
  playBtn.textContent = "▶";
  if (playTimer) { clearTimeout(playTimer); playTimer = null; }
}

function togglePlay() { playing ? pause() : play(); }

// ---- rendering ----
function renderBoard() {
  // Render top-to-bottom rank 8 → rank 1.
  const rows = [];
  for (let r = 8; r >= 1; r--) {
    for (const f of FILES) {
      const sq = sqKey(f, r);
      const piece = boardMap.get(sq);
      const cls = ["sq", isLight(f, r) ? "light" : "dark"];
      if (lastMove && lastMove.from === sq) cls.push("last-from");
      if (lastMove && lastMove.to === sq) cls.push("last-to");
      const inner = piece
        ? `<span class="piece ${piece.color === "white" ? "w" : "b"}">${PIECE_GLYPH[piece.kind]}</span>`
        : "";
      const coord = (f === "a" || r === 1)
        ? `<span class="coord">${f === "a" ? r : ""}${r === 1 && f !== "a" ? f : ""}${f === "a" && r === 1 ? "" : ""}</span>`
        : "";
      rows.push(`<div class="${cls.join(" ")}" data-sq="${sq}">${inner}${coord}</div>`);
    }
  }
  boardEl.innerHTML = rows.join("");

  $("row-white").classList.toggle("active", activeSeat === "white");
  $("row-black").classList.toggle("active", activeSeat === "black");
}

function renderEventList() {
  const items = game.event_log.map((ev, i) => {
    const cls = ["ev"];
    if (i === cursor - 1) cls.push("cur");
    else if (i < cursor) cls.push("past");
    if (ev.by === "black" || ev.color === "black" || ev.to_color === "black"
        || ev.winner === "black") cls.push("b");
    return `<div class="${cls.join(" ")}" data-i="${i}">${escapeHtml(eventLabel(ev))}</div>`;
  });
  eventsEl.innerHTML = items.join("");
  const cur = eventsEl.querySelector(".cur");
  if (cur) cur.scrollIntoView({ block: "nearest" });
}

function eventLabel(ev) {
  switch (ev.kind) {
    case "piece_moved":
      return `${ev.from}-${ev.to}` + (ev.captured ? ` ×${ev.captured}` : "");
    case "piece_captured":
      return `  captured ${ev.victim_kind} @${ev.sq}`;
    case "piece_placed":
      return `+${ev.color[0]} ${ev.piece_kind} @${ev.sq}`;
    case "piece_promoted":
      return `promote ${ev.sq} → ${ev.to}`;
    case "piece_converted":
      return `convert ${ev.sq} → ${ev.to_color}`;
    case "card_played":
      return `played ${ev.card_id || "?"}`;
    case "card_drawn":
      return `drew ${ev.count || 1}`;
    case "card_stolen":
      return `stole ${ev.card_id || "?"}`;
    case "card_burned":
      return `burned ${ev.card_id || "?"}`;
    case "turn_end":
      return `── ${ev.next}'s turn ──`;
    case "king_captured":
      return `★ king captured — ${ev.winner} wins ★`;
    default:
      return ev.kind;
  }
}

function render() {
  renderBoard();
  renderEventList();
  readoutEl.textContent = `${cursor} / ${game.event_log.length}`;
}

// ---- bootstrap ----
function slugFromPath() {
  const parts = location.pathname.split("/").filter(Boolean);
  // /chess/replay/<slug>
  return parts[parts.length - 1] || "";
}

async function bootstrap() {
  const slug = slugFromPath();
  const res = await fetch(`/chess/api/games/${slug}`);
  if (!res.ok) {
    boardEl.innerHTML = `<div style="padding:2rem;color:var(--muted);">game not found</div>`;
    return;
  }
  game = await res.json();
  $("game-meta").textContent =
    `${game.white_name} (W) vs ${game.black_name} (B) — ${game.winner || "?"} `
    + `${game.win_reason ? "(" + game.win_reason + ")" : ""}`;
  $("white-name").textContent = `${game.white_name} (white)`;
  $("black-name").textContent = `${game.black_name} (black)`;
  $("white-setup").textContent = setupSummary(game.white_setup);
  $("black-setup").textContent = setupSummary(game.black_setup);

  buildInitialBoard();
  cursor = 0;
  render();

  $("btn-start").addEventListener("click", () => jump(0));
  $("btn-prev").addEventListener("click", () => step(-1));
  $("btn-next").addEventListener("click", () => step(1));
  $("btn-end").addEventListener("click", () => jump(game.event_log.length));
  playBtn.addEventListener("click", togglePlay);
  document.addEventListener("keydown", (e) => {
    if (e.key === "ArrowRight") step(1);
    else if (e.key === "ArrowLeft") step(-1);
    else if (e.key === " ") { e.preventDefault(); togglePlay(); }
    else if (e.key === "Home") jump(0);
    else if (e.key === "End") jump(game.event_log.length);
  });
  // Click an event row to jump there.
  eventsEl.addEventListener("click", (e) => {
    const row = e.target.closest(".ev");
    if (!row) return;
    jump(Number(row.dataset.i) + 1);
  });
}

function setupSummary(setup) {
  if (!setup || !setup.length) return "(no setup)";
  const counts = {};
  for (const p of setup) counts[p.kind] = (counts[p.kind] || 0) + 1;
  const order = ["queen", "rook", "bishop", "knight", "pawn"];
  return order.filter(k => counts[k])
    .map(k => `${counts[k]}${k[0].toUpperCase()}`)
    .join(" ");
}

bootstrap();
