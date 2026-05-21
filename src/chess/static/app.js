// Hearthstone Chess — frontend (Phase 4)
// Client-side targeting walker, animations, prompt rendering.

// Use the same filled glyphs for both sides; color differentiates them.
// Outline glyphs (♔ etc.) render too thin/anemic next to the filled set.
const PIECE_GLYPH = {
  white: { king: "♚", queen: "♛", rook: "♜",
           bishop: "♝", knight: "♞", pawn: "♟" },
  black: { king: "♚", queen: "♛", rook: "♜",
           bishop: "♝", knight: "♞", pawn: "♟" },
};
const PIECE_FROM_CARD = {
  piece_pawn: "pawn", piece_knight: "knight", piece_bishop: "bishop",
  piece_rook: "rook", piece_queen: "queen",
};
const PIECE_COSTS = { pawn: 1, knight: 2, bishop: 3, rook: 5, queen: 8 };
const MATERIAL_POINTS = { pawn: 1, knight: 2, bishop: 3, rook: 5, queen: 8 };

// One-liner effect descriptions for tooltips.
const CARD_EFFECTS = {
  piece_pawn: "Place a pawn in your back two ranks.",
  piece_knight: "Place a knight in your back two ranks.",
  piece_bishop: "Place a bishop in your back two ranks.",
  piece_rook: "Place a rook in your back two ranks.",
  piece_queen: "Place a queen in your back two ranks.",
  piece_any: "Pick a piece kind, pay its cost, place in your back two ranks.",
  spell_extra_pawn_move: "Target pawn gets one extra forward step this turn.",
  spell_gain_2_gold: "Gain 2 gold this turn (no cap raise).",
  spell_draw_2: "Draw 2 cards.",
  spell_pawn_back_or_two_moves: "Choose: pawn moves backward OR two pawn moves this turn.",
  spell_play_2_pawns: "Place 2 pawns in your back two ranks.",
  spell_combine_pawns: "Sacrifice 3 pawns; place a Knight or Bishop in your back two ranks; draw a card.",
  spell_adjacent_en_passant: "Capture an enemy adjacent to your pawn. Counts as your move.",
  spell_remove_pawn_draw_3: "Sacrifice a friendly pawn; draw 3.",
  spell_modular_board: "Board edges wrap this turn. Cannot capture enemy king.",
  spell_tax_opponent: "Opponent's spells cost +3 gold next turn.",
  spell_pawn_to_rook: "Sacrifice a friendly pawn; place a rook in your back two ranks.",
  spell_remove_minor: "Remove any knight or bishop on the board; draw a card.",
  spell_forced_promotion: "Pick one of your pawns. Your OPPONENT picks Knight or Bishop; the pawn becomes that piece in place.",
  spell_discard_draw: "Discard any subset of your hand, draw one per discard.",
  spell_teleport: "Move one of your pieces to any empty square. Counts as your move.",
  spell_king_to_center: "Move a king to d4/d5/e4/e5; draw a card. Counts as move. No king capture.",
  spell_draw_double: "Draw cards equal to your current hand size.",
  spell_pawns_to_queen: "Need 5+ pawns. Remove all your pawns; place a queen in back two.",
  spell_material_to_queen: "Sacrifice friendly pieces totaling exactly 7; place a queen.",
  spell_convert_piece: "Convert an enemy non-king piece to your control. Counts as your move.",
  spell_triple_move: "This turn you move 3 distinct pieces. No king capture.",
  spell_rook_and_minor: "Place a rook AND a Knight/Bishop (modal) in your back two ranks.",
  spell_deploy_enemy_rank: "Place a Pawn or Rook on opponent's 2nd/7th rank.",
  spell_eight_or_eight: "Up to 8 pawns OR pieces totaling exactly 8 in your back two ranks.",
  spell_wipe_type: "Pick a piece kind; remove every piece of that kind (both sides).",
  spell_draw_full_no_move: "Draw to hand cap. Cannot make a chess move this turn.",
  spell_choose_opp_move: "Pick one of opponent's currently-legal moves; it resolves immediately.",
  spell_draw_rook_extra: "Draw 4; place a rook; gain an extra move. Cannot checkmate.",
  spell_quad_deploy: "Place a knight, bishop, rook, and pawn in your back two ranks.",
  spell_extra_turn: "Take another turn after this one (gold capped at 5). Cannot capture king.",
  spell_queen_and_strip: "Place a queen, then remove ≤4 material from enemy.",
  spell_echo: "Look at opponent's hand. Pick one card; you steal it AND immediately play it for free.",
  spell_free_pieces: "Draw 5. Piece cards cost 0 this turn.",
};

const $ = (id) => document.getElementById(id);
const entry = $("entry");
const game = $("game");
const nameInput = $("player-name");
const roomInput = $("room-code");
const joinBtn = $("join-btn");
const entryStatus = $("entry-status");

const roomLabel = $("room-label");
const copyBtn = $("copy-btn");
const concedeBtn = $("concede-btn");
const concedeConfirm = $("concede-confirm");
const concedeYes = $("concede-yes");
const concedeNo = $("concede-no");
const statusLine = $("status-line");
const disconnectBanner = $("disconnect-banner");

const oppNameEl = $("opp-name");
const oppDeckEl = $("opp-deck");
const oppHandSizeEl = $("opp-hand-size");
const oppGoldEl = $("opp-gold");
const oppGoldNumEl = $("opp-gold-num");
const oppHandEl = $("opp-hand");
const oppPanelEl = $("opp-panel");

const youNameEl = $("you-name");
const youDeckEl = $("you-deck");
const youHandSizeEl = $("you-hand-size");
const youGoldEl = $("you-gold");
const youGoldNumEl = $("you-gold-num");
const youPanelEl = $("you-panel");
const handEl = $("hand");
const endTurnBtn = $("end-turn-btn");
const undoBtn = $("undo-btn");
const mulliganBtn = $("mulligan-btn");
const cancelTargetingBtn = $("cancel-targeting-btn");
const mulliganWait = $("mulligan-wait");
const timerDots = $("timer-dots");
const timerNum = $("timer-num");

const boardEl = $("board");
const tweenLayerEl = $("tween-layer");
const logEl = $("log");
const bannerEl = $("banner");
const promptArea = $("prompt-area");

// ---- state ----
let ws = null;
let mySeat = null;
let lastState = null;
let prevState = null;          // for animation diffing
let lastBoardMap = new Map();  // sq -> piece (last rendered)
let selectedSq = null;         // selected own piece for chess move
let mulliganMarks = new Set();
let timerHandle = null;
let statusErrTimer = null;
let toastTimer = null;
let disconnected = false;
let reconnectTimer = null;
let lastRoom = null;
let lastName = null;

// Active targeting "play" — full client-side walk.
let casting = null;
let hoverSq = null;          // hovered friendly piece during own turn — preview legal moves
/* casting shape:
 {
   slot, card,
   targets: [] collected so far,
   targetSpecs: [],   computed target sequence
   modal: null | string,
   awaitingModalAt: null | int,  // index into targetSpecs where to insert modal
   sacSquares: [],   for material_to_queen
   stripSquares: [], for queen_and_strip
   discardSlots: [],
   pickedKind: null,   for any_piece_kind, place_any (chosen piece)
   step: int (current index in targetSpecs),
   chosenOppMove: null
 }
*/

// Server-emitted prompt (for opp_move, two_minors_opp_picks, etc.)
let serverPrompt = null;

// ---- entry ----

(function initFromUrl() {
  const params = new URLSearchParams(location.search);
  const room = params.get("room");
  const name = params.get("name");
  if (room) roomInput.value = room.toUpperCase();
  if (name) nameInput.value = name;
  if (room && name) setTimeout(() => join(), 50);
})();

joinBtn.addEventListener("click", join);
nameInput.addEventListener("keydown", (e) => { if (e.key === "Enter") roomInput.focus(); });
roomInput.addEventListener("keydown", (e) => { if (e.key === "Enter") join(); });

function join() {
  const name = (nameInput.value || "anon").trim().slice(0, 24) || "anon";
  const room = ((roomInput.value || "TEST").trim().toUpperCase()) || "TEST";
  if (room.length > 8) {
    entryStatus.textContent = "room code too long (max 8 chars)";
    return;
  }
  entryStatus.textContent = "connecting...";
  joinBtn.disabled = true;
  lastRoom = room;
  lastName = name;
  connect(room, name);
}

function connect(room, name) {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const url = new URL("ws", location.href);
  url.protocol = proto + ":";
  url.searchParams.set("room", room);
  url.searchParams.set("name", name);
  ws = new WebSocket(url);

  ws.addEventListener("open", () => {
    entry.hidden = true;
    game.hidden = false;
    roomLabel.textContent = room;
    if (disconnected) {
      disconnected = false;
      disconnectBanner.hidden = true;
    }
  });

  ws.addEventListener("message", (e) => {
    let msg;
    try { msg = JSON.parse(e.data); } catch { return; }
    handleMessage(msg);
  });

  ws.addEventListener("close", () => {
    joinBtn.disabled = false;
    if (lastState) {
      // mid-game: show banner, retry.
      disconnected = true;
      disconnectBanner.hidden = false;
      if (reconnectTimer) clearTimeout(reconnectTimer);
      reconnectTimer = setTimeout(() => connect(lastRoom, lastName), 2000);
    } else {
      entryStatus.textContent = "disconnected";
      entry.hidden = false;
      game.hidden = true;
    }
  });

  ws.addEventListener("error", () => {
    if (!lastState) {
      entryStatus.textContent = "connection error";
      joinBtn.disabled = false;
    }
  });
}

// ---- top buttons ----

copyBtn.addEventListener("click", () => {
  const url = new URL(location.href);
  url.searchParams.set("room", roomLabel.textContent);
  if (navigator.clipboard) navigator.clipboard.writeText(url.toString());
  toast("link copied");
});

const openOppBtn = $("open-opp-btn");
if (openOppBtn) {
  openOppBtn.addEventListener("click", () => {
    const url = new URL(location.href);
    url.searchParams.set("room", roomLabel.textContent);
    // Pick a name that won't collide with our own; the server's
    // reconnect-by-name code is keyed on exact match.
    const oppName = lastName === "p1" ? "p2"
                  : lastName === "a"  ? "b"
                  : lastName === "alice" ? "bob"
                  : `${lastName}-opp`;
    url.searchParams.set("name", oppName);
    window.open(url.toString(), "_blank");
  });
}

concedeBtn.addEventListener("click", () => {
  concedeBtn.hidden = true;
  concedeConfirm.hidden = false;
});
concedeNo.addEventListener("click", () => {
  concedeConfirm.hidden = true;
  concedeBtn.hidden = false;
});
concedeYes.addEventListener("click", () => {
  if (!ws || ws.readyState !== 1) return;
  ws.send(JSON.stringify({ type: "concede" }));
  concedeConfirm.hidden = true;
  concedeBtn.hidden = false;
});

endTurnBtn.addEventListener("click", () => {
  if (!ws || ws.readyState !== 1) return;
  if (casting) cancelCasting();
  ws.send(JSON.stringify({ type: "end_turn" }));
});

undoBtn.addEventListener("click", () => {
  if (!ws || ws.readyState !== 1) return;
  if (casting) cancelCasting();
  ws.send(JSON.stringify({ type: "undo_move" }));
  selectedSq = null;
});

mulliganBtn.addEventListener("click", () => {
  if (!ws || ws.readyState !== 1) return;
  const slots = [...mulliganMarks].sort((a, b) => a - b);
  ws.send(JSON.stringify({ type: "mulligan", redraw_slots: slots }));
  mulliganMarks.clear();
  mulliganBtn.hidden = true;
  mulliganWait.hidden = false;
});

cancelTargetingBtn.addEventListener("click", () => cancelCasting());

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") {
    if (casting) cancelCasting();
    else if (selectedSq) { selectedSq = null; renderBoardOnly(); }
  }
});
document.addEventListener("contextmenu", (e) => {
  // If a click landed on a board square, treat right-click as
  // "toggle annotation" so players can highlight squares for their
  // opponent. Casting/selection are cleared via Escape instead.
  const cell = e.target.closest && e.target.closest(".sq");
  if (cell) {
    e.preventDefault();
    if (!ws || ws.readyState !== 1) return;
    ws.send(JSON.stringify({ type: "toggle_annotation", square: cell.dataset.sq }));
    return;
  }
  if (casting || selectedSq) {
    e.preventDefault();
    if (casting) cancelCasting();
    else { selectedSq = null; renderBoardOnly(); }
  }
});

// ---- WS messages ----

function handleMessage(msg) {
  if (msg.type === "welcome") {
    mySeat = msg.your_seat;
    window.mySeat = mySeat;
    return;
  }
  if (msg.type === "error") {
    flashStatusErr(msg.text || "error");
    return;
  }
  if (msg.type === "event") {
    handleEvent(msg);
    return;
  }
  if (msg.type === "prompt") {
    serverPrompt = msg;
    renderPromptArea();
    return;
  }
  if (msg.type === "state") {
    prevState = lastState;
    lastState = msg;
    if (msg.you && msg.you.seat) { mySeat = msg.you.seat; window.mySeat = mySeat; }
    window.lastState = lastState;
    window.lastPhase = lastState.phase;
    render();
    maybeAutoEchoCast();
  }
}

function maybeAutoEchoCast() {
  if (!lastState || casting) return;
  const echoId = lastState.you && lastState.you.echo_free_instance_id;
  if (!echoId) return;
  const card = (lastState.hand || []).find(c => c.instance_id === echoId);
  if (!card) return;
  // Auto-initiate the cast for the stolen card. Player can still cancel
  // (Escape / cancel button) — the card stays in hand, free until end of turn.
  beginCasting(card);
}

// ---- animations / events ----

function handleEvent(msg) {
  const k = msg.kind;
  if (k === "piece_moved") {
    animateMove(msg.from, msg.to);
  } else if (k === "piece_captured") {
    fadeSquare(msg.sq, "fade-out");
  } else if (k === "piece_placed") {
    // delayed fade-in handled in render when piece appears
    setTimeout(() => fadeSquare(msg.sq, "fade-in"), 0);
  } else if (k === "card_played") {
    // spell-zone overlay — pieced together from card info if we know it
    flashSpellOverlay(msg);
  } else if (k === "king_captured") {
    kingFlash();
  } else if (k === "turn_end") {
    showTurnBanner(msg.next === mySeat ? "Your Turn" : "Opponent's Turn");
  }
}

function squareCenter(sq) {
  // Returns {x,y} in board-wrap coordinates.
  const cell = boardEl.querySelector(`.sq[data-sq="${sq}"]`);
  if (!cell) return null;
  const wrap = boardEl.parentElement;
  const cb = cell.getBoundingClientRect();
  const wb = wrap.getBoundingClientRect();
  return { x: cb.left - wb.left, y: cb.top - wb.top, w: cb.width, h: cb.height };
}

function animateMove(from, to) {
  // Tween a ghost piece on the overlay layer; the underlying re-render will
  // immediately show the piece at `to`. To make this feel smooth we briefly
  // hide the destination's piece, animate, then let render() take over.
  const piece = lastBoardMap.get(from);
  if (!piece) return;
  const a = squareCenter(from);
  const b = squareCenter(to);
  if (!a || !b) return;
  const ghost = document.createElement("div");
  ghost.className = `tween-piece ${piece.color}`;
  ghost.textContent = PIECE_GLYPH[piece.color][piece.kind];
  ghost.style.transform = `translate(${a.x}px, ${a.y}px)`;
  tweenLayerEl.appendChild(ghost);
  // Force layout, then move.
  requestAnimationFrame(() => {
    ghost.style.transform = `translate(${b.x}px, ${b.y}px)`;
  });
  setTimeout(() => ghost.remove(), 240);
}

function fadeSquare(sq, cls) {
  const cell = boardEl.querySelector(`.sq[data-sq="${sq}"]`);
  if (!cell) return;
  cell.classList.add(cls);
  setTimeout(() => cell.classList.remove(cls), 250);
}

function flashSpellOverlay(msg) {
  if (msg.by !== mySeat) return; // only show for caster
  // Try to grab last-played card info from prev hand.
  const card_id = msg.card_id;
  const card = {
    name: (cardInfo(card_id) || {}).name || card_id,
    cost: (cardInfo(card_id) || {}).cost || 0,
  };
  const handRect = handEl.getBoundingClientRect();
  const boardRect = boardEl.getBoundingClientRect();
  const zone = document.createElement("div");
  zone.id = "spell-zone";
  zone.style.left = (boardRect.left + boardRect.width / 2 - 48) + "px";
  zone.style.top = (boardRect.top + boardRect.height / 2 - 70) + "px";
  const c = document.createElement("div");
  c.className = "spell-card";
  c.innerHTML = `<div class="cost">${card.cost === -1 ? "?" : card.cost}</div>
                 <div class="name">${escapeHtml(card.name)}</div>
                 <div class="effect-text">${escapeHtml(CARD_EFFECTS[card_id] || "")}</div>`;
  // small cost ball inline (CSS for .cost not here in this scope — quick inline)
  const costEl = c.querySelector(".cost");
  if (costEl) {
    Object.assign(costEl.style, {
      position: "absolute", top: "3px", left: "3px",
      width: "18px", height: "18px", borderRadius: "50%",
      background: "var(--gold)", color: "var(--bg)",
      display: "flex", alignItems: "center", justifyContent: "center",
      fontWeight: "600", fontSize: "0.72rem",
    });
  }
  c.style.position = "relative";
  c.querySelector(".name").style.marginTop = "20px";
  c.querySelector(".name").style.fontSize = "0.7rem";
  c.querySelector(".name").style.textAlign = "center";
  c.querySelector(".effect-text").style.fontSize = "0.6rem";
  c.querySelector(".effect-text").style.color = "var(--muted)";
  c.querySelector(".effect-text").style.padding = "4px";
  c.querySelector(".effect-text").style.textAlign = "center";
  c.querySelector(".effect-text").style.marginTop = "4px";
  zone.appendChild(c);
  document.body.appendChild(zone);
  setTimeout(() => zone.remove(), 400);
}

function cardInfo(card_id) {
  // Lookup minimal info using effect-text + a name table embedded in CARD_NAMES.
  return CARD_NAMES[card_id];
}

const CARD_NAMES = {
  piece_pawn: { name: "Pawn", cost: 1 },
  piece_knight: { name: "Knight", cost: 2 },
  piece_bishop: { name: "Bishop", cost: 3 },
  piece_rook: { name: "Rook", cost: 5 },
  piece_queen: { name: "Queen", cost: 8 },
  piece_any: { name: "Any Piece", cost: -1 },
  spell_extra_pawn_move: { name: "Extra Pawn Step", cost: 1 },
  spell_gain_2_gold: { name: "Gain 2 Gold", cost: 1 },
  spell_draw_2: { name: "Draw 2", cost: 1 },
  spell_pawn_back_or_two_moves: { name: "Pawn Back / Two Moves", cost: 2 },
  spell_play_2_pawns: { name: "Play 2 Pawns", cost: 2 },
  spell_combine_pawns: { name: "Combine 3 Pawns", cost: 2 },
  spell_adjacent_en_passant: { name: "Adjacent En Passant", cost: 3 },
  spell_remove_pawn_draw_3: { name: "Sacrifice Pawn, Draw 3", cost: 3 },
  spell_modular_board: { name: "Modular Board", cost: 3 },
  spell_tax_opponent: { name: "Spell Tax", cost: 4 },
  spell_pawn_to_rook: { name: "Pawn into Rook", cost: 4 },
  spell_remove_minor: { name: "Remove a Minor", cost: 4 },
  spell_forced_promotion: { name: "Forced Promotion", cost: 4 },
  spell_discard_draw: { name: "Discard for Draw", cost: 5 },
  spell_teleport: { name: "Teleport", cost: 5 },
  spell_king_to_center: { name: "King to Center", cost: 5 },
  spell_draw_double: { name: "Draw Hand Doubled", cost: 6 },
  spell_pawns_to_queen: { name: "5 Pawns into Queen", cost: 6 },
  spell_material_to_queen: { name: "7 Material into Queen", cost: 7 },
  spell_triple_move: { name: "Triple Move", cost: 7 },
  spell_convert_piece: { name: "Convert Piece", cost: 8 },
  spell_rook_and_minor: { name: "Rook + Minor", cost: 8 },
  spell_deploy_enemy_rank: { name: "Deploy on Enemy Rank", cost: 8 },
  spell_eight_or_eight: { name: "Up to 8 Pawns / 8 Material", cost: 8 },
  spell_wipe_type: { name: "Wipe Type", cost: 9 },
  spell_draw_full_no_move: { name: "Draw to Full (No Move)", cost: 9 },
  spell_choose_opp_move: { name: "Choose Opponent Move", cost: 9 },
  spell_draw_rook_extra: { name: "Draw 4 + Rook + Extra Move", cost: 10 },
  spell_quad_deploy: { name: "Quadruple Deploy", cost: 10 },
  spell_extra_turn: { name: "Extra Turn", cost: 10 },
  spell_queen_and_strip: { name: "Queen + Strip Material", cost: 10 },
  spell_echo: { name: "Echo", cost: 10 },
  spell_free_pieces: { name: "Free Pieces", cost: 10 },
};

function showTurnBanner(text) {
  const old = document.getElementById("turn-banner");
  if (old) old.remove();
  const el = document.createElement("div");
  el.id = "turn-banner";
  el.textContent = text;
  document.body.appendChild(el);
  setTimeout(() => el.remove(), 700);
}

function kingFlash() {
  const old = document.getElementById("king-flash");
  if (old) old.remove();
  const el = document.createElement("div");
  el.id = "king-flash";
  document.body.appendChild(el);
  setTimeout(() => el.remove(), 420);
}

// ---- rendering ----

function render() {
  if (!lastState) return;
  const s = lastState;
  const youSeat = mySeat || (s.you && s.you.seat) || "spectator";
  const youIsWhite = youSeat === "white";
  const youInfo = youIsWhite ? s.white : s.black;
  const oppInfo = youIsWhite ? s.black : s.white;
  const youName = youIsWhite ? s.white_name : s.black_name;
  const oppName = youIsWhite ? s.black_name : s.white_name;

  youNameEl.textContent = (youName || youSeat) + (youSeat === "spectator" ? " (spec)" : "");
  oppNameEl.textContent = oppName || "(waiting...)";

  if (youInfo) {
    youDeckEl.textContent = youInfo.deck_size;
    youHandSizeEl.textContent = youInfo.hand_size;
    renderGoldDots(youGoldEl, youInfo.gold, youInfo.gold_cap);
    youGoldNumEl.textContent = `${youInfo.gold}/${youInfo.gold_cap}`;
  }
  if (oppInfo) {
    oppDeckEl.textContent = oppInfo.deck_size;
    oppHandSizeEl.textContent = oppInfo.hand_size;
    renderGoldDots(oppGoldEl, oppInfo.gold, oppInfo.gold_cap);
    oppGoldNumEl.textContent = `${oppInfo.gold}/${oppInfo.gold_cap}`;
  }

  oppHandEl.innerHTML = "";
  for (let i = 0; i < (s.opp_hand_size || 0); i++) {
    const b = document.createElement("div");
    b.className = "opp-card-back";
    oppHandEl.appendChild(b);
  }

  oppPanelEl.classList.toggle("active", s.active_seat && s.active_seat !== youSeat && s.phase === "PLAYING");
  youPanelEl.classList.toggle("active", s.active_seat === youSeat && s.phase === "PLAYING");

  renderBoard(s.board || [], youSeat);
  renderStatusLine(s, youSeat);
  renderHand(s, youSeat);

  // Mulligan UI
  if (s.phase === "MULLIGAN" && (youSeat === "white" || youSeat === "black")) {
    endTurnBtn.hidden = true;
    if (mulliganWait.hidden) {
      mulliganBtn.hidden = false;
      mulliganBtn.textContent = `Replace ${mulliganMarks.size} cards`;
    }
  } else {
    endTurnBtn.hidden = false;
    mulliganBtn.hidden = true;
    mulliganWait.hidden = true;
    if (s.phase !== "MULLIGAN") mulliganMarks.clear();
  }

  cancelTargetingBtn.hidden = !casting;

  const myTurn = s.phase === "PLAYING" && s.active_seat === youSeat;
  const meState = (youSeat && s[youSeat]) || {};
  const mustMove = myTurn && !meState.has_acted_this_turn && !meState.no_chess_move_this_turn;
  const myInCheck = youSeat === "white" ? s.white_in_check : s.black_in_check;
  endTurnBtn.disabled = !myTurn || (myTurn && myInCheck);
  let endLabel = "End Turn";
  if (myTurn && myInCheck) endLabel = "End Turn (in check — get out first)";
  else if (mustMove) endLabel = "End Turn (must move first)";
  endTurnBtn.textContent = endLabel;
  endTurnBtn.classList.toggle("warn", mustMove || (myTurn && myInCheck));

  // Undo button only when the server says we can undo (our turn, snapshot
  // exists, no card mid-resolution).
  undoBtn.hidden = !s.can_undo;

  // Log
  logEl.innerHTML = "";
  for (const e of s.log || []) {
    const li = document.createElement("li");
    li.textContent = e.text;
    logEl.appendChild(li);
  }
  logEl.scrollTop = logEl.scrollHeight;

  updateTimer(s.turn_deadline_ms);

  if (s.phase === "DONE" && s.winner) {
    const won = s.winner === youSeat;
    const reason = s.win_reason === "concede"
      ? `${s.winner === "white" ? s.black_name || "black" : s.white_name || "white"} conceded`
      : `${s.winner} captured the king`;
    bannerEl.hidden = false;
    bannerEl.innerHTML = `<div>${won ? "YOU WIN" : "GAME OVER"}</div>
      <div class="sub">${reason}</div>
      <button id="rematch-btn">rematch</button>`;
    const rematchBtn = document.getElementById("rematch-btn");
    if (rematchBtn) {
      rematchBtn.addEventListener("click", () => {
        if (!ws || ws.readyState !== 1) { location.reload(); return; }
        ws.send(JSON.stringify({ type: "new_game" }));
      });
    }
  } else {
    bannerEl.hidden = true;
  }

  renderPromptArea();
}

function renderBoardOnly() {
  if (!lastState) return;
  renderBoard(lastState.board || [], mySeat);
  renderPromptArea();
}

function renderGoldDots(el, cur, cap) {
  cap = Math.max(0, cap | 0);
  cur = Math.max(0, Math.min(cap, cur | 0));
  el.innerHTML = "";
  for (let i = 0; i < cap; i++) {
    const d = document.createElement("span");
    d.className = "gold-dot" + (i < cur ? "" : " empty");
    el.appendChild(d);
  }
}

function renderBoard(pieces, youSeat) {
  boardEl.innerHTML = "";
  const flip = youSeat === "black";
  const byKey = new Map();
  for (const p of pieces) byKey.set(p.sq, p);
  lastBoardMap = byKey;

  const validTargets = casting ? validSquaresForCurrentStep(byKey) : null;

  // Legal move highlights: prefer the selected piece; otherwise show a
  // hover preview if the cursor is over one of your own movable pieces.
  const myTurn = lastState && lastState.phase === "PLAYING" && lastState.active_seat === youSeat;
  let moveTargets = null;
  let movePieceSq = null;
  if (!casting && myTurn) {
    const showSq = selectedSq || hoverSq;
    if (showSq) {
      const pc = byKey.get(showSq);
      if (pc && pc.color === youSeat && !pc.placed_this_turn) {
        moveTargets = legalDestinations(showSq, byKey, youSeat);
        movePieceSq = showSq;
      }
    }
  }

  // Check highlight: which king square is under attack.
  const checkedSquares = new Set();
  if (lastState) {
    if (lastState.white_in_check) {
      for (const [sq, pc] of byKey) if (pc.kind === "king" && pc.color === "white") checkedSquares.add(sq);
    }
    if (lastState.black_in_check) {
      for (const [sq, pc] of byKey) if (pc.kind === "king" && pc.color === "black") checkedSquares.add(sq);
    }
  }

  // Square annotations (visible to both players).
  const myAnno = new Set((lastState && lastState.annotations && lastState.annotations[youSeat]) || []);
  const oppSeatStr = youSeat === "white" ? "black" : "white";
  const oppAnno = new Set((lastState && lastState.annotations && lastState.annotations[oppSeatStr]) || []);

  for (let rIdx = 0; rIdx < 8; rIdx++) {
    for (let fIdx = 0; fIdx < 8; fIdx++) {
      const rank = flip ? rIdx + 1 : 8 - rIdx;
      const file = flip ? 7 - fIdx : fIdx;
      const fileChar = String.fromCharCode(97 + file);
      const sq = `${fileChar}${rank}`;
      const cell = document.createElement("div");
      cell.className = "sq " + (((rank + file) % 2 === 0) ? "dark" : "light");
      cell.dataset.sq = sq;

      if (fIdx === 0) {
        const rl = document.createElement("span");
        rl.className = "rank-lbl";
        rl.textContent = rank;
        cell.appendChild(rl);
      }
      if (rIdx === 7) {
        const fl = document.createElement("span");
        fl.className = "file-lbl";
        fl.textContent = fileChar;
        cell.appendChild(fl);
      }

      const pc = byKey.get(sq);
      if (pc) {
        const g = document.createElement("span");
        g.className = `piece ${pc.color}`;
        if (pc.placed_this_turn) g.classList.add("sick");
        g.textContent = PIECE_GLYPH[pc.color][pc.kind];
        cell.appendChild(g);
      }

      if (selectedSq === sq) cell.classList.add("selected");
      else if (movePieceSq === sq && hoverSq === sq) cell.classList.add("hover-src");
      if (checkedSquares.has(sq)) cell.classList.add("in-check");

      if (validTargets && validTargets.has(sq)) {
        if (pc) cell.classList.add("target-piece");
        else cell.classList.add("target");
      }
      if (moveTargets && moveTargets.has(sq)) {
        if (pc) cell.classList.add("move-capture");
        else cell.classList.add("move-dest");
      }
      // Sub-marks (sac picks, strip picks)
      if (casting) {
        const sacSet = new Set(casting.sacSquares || []);
        const stripSet = new Set(casting.stripSquares || []);
        if (sacSet.has(sq) || stripSet.has(sq)) cell.classList.add("marked-target");
        // Already-collected square placements highlighted
        for (const t of casting.targets || []) {
          if (t && t.square === sq) cell.classList.add("marked-target");
        }
      }
      if (myAnno.has(sq)) cell.classList.add("anno-mine");
      if (oppAnno.has(sq)) cell.classList.add("anno-opp");

      cell.addEventListener("click", () => onSquareClick(sq, byKey));
      cell.addEventListener("mouseenter", () => onSquareHover(sq, byKey));
      cell.addEventListener("mouseleave", () => onSquareHover(null, byKey));
      boardEl.appendChild(cell);
    }
  }
}

// ---- client-side legal-destination calc (for move-hint highlights) ----
// Mirrors src/chess/board.py logic minus EP (server enforces). Modular-board
// mode is rare and we conservatively skip it — server still validates.

const _KNIGHT = [[1,2],[2,1],[-1,2],[-2,1],[1,-2],[2,-1],[-1,-2],[-2,-1]];
const _KING = [[1,0],[-1,0],[0,1],[0,-1],[1,1],[1,-1],[-1,1],[-1,-1]];
const _RAYS = {
  rook: [[1,0],[-1,0],[0,1],[0,-1]],
  bishop: [[1,1],[1,-1],[-1,1],[-1,-1]],
  queen: [[1,0],[-1,0],[0,1],[0,-1],[1,1],[1,-1],[-1,1],[-1,-1]],
};

function legalDestinations(src, byKey, mySeat) {
  const pc = byKey.get(src);
  if (!pc) return new Set();
  const [f, r] = sqToFR(src);
  const out = new Set();
  const isMine = (sq) => byKey.has(sq) && byKey.get(sq).color === pc.color;
  const isEnemy = (sq) => byKey.has(sq) && byKey.get(sq).color !== pc.color;

  if (pc.kind === "knight" || pc.kind === "king") {
    const deltas = pc.kind === "knight" ? _KNIGHT : _KING;
    for (const [df, dr] of deltas) {
      const nf = f + df, nr = r + dr;
      if (nf < 0 || nf > 7 || nr < 0 || nr > 7) continue;
      const sq = frToSq(nf, nr);
      if (!isMine(sq)) out.add(sq);
    }
    return out;
  }
  if (pc.kind === "pawn") {
    const dir = pc.color === "white" ? 1 : -1;
    const fwd1 = frToSq(f, r + dir);
    if (r + dir >= 0 && r + dir <= 7 && !byKey.has(fwd1)) {
      out.add(fwd1);
      const fwd2 = frToSq(f, r + 2 * dir);
      if (!pc.has_moved && r + 2*dir >= 0 && r + 2*dir <= 7 && !byKey.has(fwd2)) {
        out.add(fwd2);
      }
    }
    for (const df of [-1, 1]) {
      const nf = f + df, nr = r + dir;
      if (nf < 0 || nf > 7 || nr < 0 || nr > 7) continue;
      const sq = frToSq(nf, nr);
      if (isEnemy(sq)) out.add(sq);
    }
    return out;
  }
  if (_RAYS[pc.kind]) {
    for (const [df, dr] of _RAYS[pc.kind]) {
      let nf = f, nr = r;
      for (let i = 0; i < 8; i++) {
        nf += df; nr += dr;
        if (nf < 0 || nf > 7 || nr < 0 || nr > 7) break;
        const sq = frToSq(nf, nr);
        if (isMine(sq)) break;
        out.add(sq);
        if (isEnemy(sq)) break;
      }
    }
    return out;
  }
  return out;
}

function renderStatusLine(s, youSeat) {
  if (statusLine.classList.contains("err")) return;
  statusLine.classList.remove("target");
  let msg = "";
  if (casting) {
    msg = currentTargetingPrompt() || "targeting...";
    statusLine.classList.add("target");
  } else if (serverPrompt) {
    msg = "answer prompt at right";
    statusLine.classList.add("target");
  } else if (s.phase === "LOBBY") msg = "waiting for opponent...";
  else if (s.phase === "MULLIGAN") {
    msg = "mulligan — click cards to mark for redraw, then submit";
  } else if (s.phase === "PLAYING") {
    const yourTurn = s.active_seat === youSeat;
    msg = `turn ${s.turn_number} — ${yourTurn ? "your turn" : "opponent's turn"}`;
  } else if (s.phase === "DONE") {
    msg = `game over — ${s.winner} wins`;
  }
  statusLine.textContent = msg;
}

function renderHand(s, youSeat) {
  clearTooltip();
  handEl.innerHTML = "";
  if (youSeat !== "white" && youSeat !== "black") return;
  for (const card of s.hand || []) {
    const el = makeCardEl(card, s);
    handEl.appendChild(el);
  }
}

function makeCardEl(card, s) {
  const el = document.createElement("div");
  el.className = "card";
  if (mySeat === "black") el.classList.add("black-side");

  const cost = document.createElement("div");
  cost.className = "cost";
  cost.textContent = card.cost === -1 ? "?" : card.cost;
  el.appendChild(cost);

  const name = document.createElement("div");
  name.className = "name";
  name.textContent = card.name;
  el.appendChild(name);

  if (card.type === "piece") {
    const g = document.createElement("div");
    g.className = "glyph";
    const kind = PIECE_FROM_CARD[card.card_id];
    g.textContent = kind ? PIECE_GLYPH[mySeat || "white"][kind] : "?";
    el.appendChild(g);
  } else {
    const t = document.createElement("div");
    t.className = "effect-text";
    t.textContent = CARD_EFFECTS[card.card_id] || "spell";
    el.appendChild(t);
  }

  const tag = document.createElement("div");
  tag.className = "tag";
  tag.textContent = card.type === "piece" ? "Piece" : "Spell";
  el.appendChild(tag);

  // Mulligan / discard markers. Once we've submitted (mulliganWait is
  // visible) we're just waiting for the opponent — don't keep the
  // highlight or the toggle handler around.
  if (s.phase === "MULLIGAN" && mulliganWait.hidden) {
    if (mulliganMarks.has(card.slot)) el.classList.add("selected-mull");
    el.addEventListener("click", () => {
      if (mulliganMarks.has(card.slot)) mulliganMarks.delete(card.slot);
      else mulliganMarks.add(card.slot);
      render();
    });
    attachTooltip(el, card);
    return el;
  }
  if (s.phase === "MULLIGAN") {
    attachTooltip(el, card);
    return el;
  }
  if (casting && casting.card.card_id === "spell_discard_draw" && casting.discardPicking) {
    if (casting.discardSlots.has(card.slot)) el.classList.add("selected-discard");
    el.addEventListener("click", () => {
      if (casting.discardSlots.has(card.slot)) casting.discardSlots.delete(card.slot);
      else casting.discardSlots.add(card.slot);
      render();
    });
    attachTooltip(el, card);
    return el;
  }

  const myTurn = s.phase === "PLAYING" && s.active_seat === mySeat;
  const targetable = isCardTargetable(card, s);
  const blocked = casting !== null;

  if (!card.playable || !myTurn || !targetable || blocked) {
    el.classList.add("unaffordable");
  } else {
    el.classList.add("playable");
  }
  if (casting && casting.slot === card.slot) {
    el.classList.add("casting");
  }

  el.addEventListener("click", () => {
    if (blocked && (!casting || casting.slot !== card.slot)) {
      flashStatusErr("finish current card first");
      return;
    }
    if (!card.playable || !myTurn || !targetable) {
      flashStatusErr("can't play this card now");
      return;
    }
    beginCasting(card);
  });

  attachTooltip(el, card);
  return el;
}

// Single shared tooltip element. Using one DOM node (rather than per-card
// listeners that create/remove their own) survives card re-renders: when
// the hand redraws mid-hover the orphaned tip used to linger forever.
let sharedTip = null;
function clearTooltip() {
  if (sharedTip) { sharedTip.remove(); sharedTip = null; }
}
function attachTooltip(el, card) {
  el.addEventListener("mouseenter", (e) => {
    clearTooltip();
    sharedTip = document.createElement("div");
    sharedTip.className = "card-tip";
    sharedTip.innerHTML = `<div class="tip-name">${escapeHtml(card.name)} <span class="tip-cost">${card.cost === -1 ? "?" : card.cost}g</span></div>
                     <div class="tip-effect">${escapeHtml(CARD_EFFECTS[card.card_id] || "")}</div>`;
    document.body.appendChild(sharedTip);
    moveTip(e);
  });
  el.addEventListener("mousemove", moveTip);
  el.addEventListener("mouseleave", clearTooltip);
  // Belt-and-braces: if the element itself is removed from the DOM while
  // the mouse is still over it (re-render), mouseleave never fires.
  el.addEventListener("click", clearTooltip);
}
function moveTip(e) {
  if (!sharedTip) return;
  const x = Math.min(e.clientX + 14, window.innerWidth - 240);
  const y = Math.min(e.clientY + 14, window.innerHeight - 80);
  sharedTip.style.left = x + "px";
  sharedTip.style.top = y + "px";
}

// ---- targeting playability ----

function isCardTargetable(card, s) {
  // Front-end pre-check: does the card have *any* legal way to be played?
  if (!lastBoardMap || !mySeat) return true;
  const friendly = []; const pawns = []; const enemy = []; const minors = [];
  let friendlyPieceCount = 0, queenCnt = 0, rookCnt = 0, bishopCnt = 0, knightCnt = 0;
  for (const [sq, pc] of lastBoardMap.entries()) {
    if (pc.color === mySeat) {
      friendly.push(sq); friendlyPieceCount++;
      if (pc.kind === "pawn") pawns.push(sq);
      if (pc.kind === "queen") queenCnt++;
      if (pc.kind === "rook") rookCnt++;
      if (pc.kind === "bishop") bishopCnt++;
      if (pc.kind === "knight") knightCnt++;
    } else if (pc.color !== mySeat) enemy.push(sq);
    if (pc.kind === "knight" || pc.kind === "bishop") minors.push(sq);
  }
  const back2 = backTwoSquares(mySeat).filter(sq => !lastBoardMap.has(sq));
  const pawnZone = pawnZoneSquares(mySeat).filter(sq => !lastBoardMap.has(sq));
  switch (card.card_id) {
    case "spell_combine_pawns": return pawns.length >= 3 && back2.length >= 1;
    case "spell_pawns_to_queen": return pawns.length >= 5 && back2.length >= 1;
    case "spell_play_2_pawns": return pawnZone.length >= 2;
    case "spell_remove_pawn_draw_3":
    case "spell_extra_pawn_move":
      return pawns.length >= 1;
    case "spell_pawn_to_rook": return pawns.length >= 1 && back2.length >= 1;
    case "spell_remove_minor": return minors.length >= 1;
    case "spell_convert_piece": return enemy.filter(sq => lastBoardMap.get(sq).kind !== "king").length >= 1;
    case "spell_teleport": return friendly.length >= 1;
    case "spell_adjacent_en_passant": {
      for (const ps of pawns) {
        const [f, r] = sqToFR(ps);
        for (let df = -1; df <= 1; df++) for (let dr = -1; dr <= 1; dr++) {
          if (!df && !dr) continue;
          const nf = f + df, nr = r + dr;
          if (nf < 0 || nf > 7 || nr < 0 || nr > 7) continue;
          const nsq = frToSq(nf, nr);
          const epc = lastBoardMap.get(nsq);
          if (epc && epc.color !== mySeat) return true;
        }
      }
      return false;
    }
    case "spell_material_to_queen": {
      // need a subset summing to exactly 7
      const vals = friendly.filter(sq => lastBoardMap.get(sq).kind !== "king")
        .map(sq => MATERIAL_POINTS[lastBoardMap.get(sq).kind] || 0);
      return back2.length >= 1 && subsetSumExists(vals, 7);
    }
    case "spell_queen_and_strip": return back2.length >= 1; // strip optional (≤4)
    case "spell_eight_or_eight": return back2.length >= 1 || pawnZone.length >= 1;
    case "spell_quad_deploy": return back2.length >= 3 && pawnZone.length >= 1;
    case "spell_rook_and_minor": return back2.length >= 2;
    case "spell_forced_promotion": return pawns.length >= 1;
    case "spell_deploy_enemy_rank": return oppRankSquares(mySeat).some(sq => !lastBoardMap.has(sq));
    case "spell_draw_rook_extra": return back2.length >= 1;
    case "spell_king_to_center": {
      const central = ["d4","d5","e4","e5"].filter(sq => !lastBoardMap.has(sq));
      return central.length >= 1; // a king always exists
    }
    case "piece_pawn": return pawnZone.length >= 1;
    case "piece_knight": case "piece_bishop":
    case "piece_rook": case "piece_queen":
      return back2.length >= 1;
    case "piece_any":
      // playable as long as ANY piece kind has a legal spot
      return back2.length >= 1 || pawnZone.length >= 1;
  }
  return true;
}

function subsetSumExists(arr, target) {
  // DP up to target
  if (target < 0) return false;
  const dp = new Array(target + 1).fill(false);
  dp[0] = true;
  for (const v of arr) {
    if (v <= 0 || v > target) continue;
    for (let i = target; i >= v; i--) dp[i] = dp[i] || dp[i - v];
  }
  return dp[target];
}

function backTwoSquares(seat) {
  const ranks = seat === "white" ? [1, 2] : [7, 8];
  const out = [];
  for (const r of ranks) for (let f = 0; f < 8; f++) out.push(frToSq(f, r - 1));
  return out;
}
function pawnZoneSquares(seat) {
  // Pawns deploy one rank further forward than other pieces.
  const ranks = seat === "white" ? [2, 3] : [7, 6];
  const out = [];
  for (const r of ranks) for (let f = 0; f < 8; f++) out.push(frToSq(f, r - 1));
  return out;
}
function placementZoneFor(seat, kind) {
  return kind === "pawn" ? pawnZoneSquares(seat) : backTwoSquares(seat);
}
function oppRankSquares(seat) {
  const r = seat === "white" ? 7 : 2;
  const out = [];
  for (let f = 0; f < 8; f++) out.push(frToSq(f, r - 1));
  return out;
}
function sqToFR(sq) {
  return [sq.charCodeAt(0) - 97, parseInt(sq[1], 10) - 1];
}
function frToSq(f, r) {
  return String.fromCharCode(97 + f) + (r + 1);
}

// ---- chess move (board interaction) ----

function onSquareHover(sq, byKey) {
  if (!lastState || lastState.phase !== "PLAYING") return;
  if (casting) return;
  // Only preview if it's your turn and the hovered square has one of your
  // own movable pieces. Hover preview shouldn't fight with a click-selected
  // piece — if something is selected, we leave hoverSq null and the
  // selected-piece path drives the highlights.
  const want = (sq && lastState.active_seat === mySeat && !selectedSq) ? sq : null;
  if (want === hoverSq) return;
  hoverSq = want;
  renderBoardOnly();
}

function onSquareClick(sq, byKey) {
  if (!lastState) return;
  const s = lastState;
  const youSeat = mySeat;
  if (s.phase !== "PLAYING") return;

  // Targeting in progress: handle the click within the targeting machine.
  if (casting) {
    handleTargetClick(sq);
    return;
  }
  // Server-emitted prompt: choose_opp_move is handled via list, not board click.
  if (serverPrompt) return;

  if (s.active_seat !== youSeat) {
    flashStatusErr("not your turn");
    return;
  }

  const pc = byKey.get(sq);
  if (selectedSq === null) {
    if (!pc || pc.color !== youSeat) return;
    selectedSq = sq;
    renderBoardOnly();
    return;
  }
  if (sq === selectedSq) { selectedSq = null; renderBoardOnly(); return; }
  if (pc && pc.color === youSeat) { selectedSq = sq; renderBoardOnly(); return; }
  // Move attempt — promotion: if it's a pawn going to last rank, popover.
  const movingPiece = byKey.get(selectedSq);
  if (movingPiece && movingPiece.kind === "pawn") {
    const lastRank = (movingPiece.color === "white") ? "8" : "1";
    if (sq[1] === lastRank) {
      promotionPicker(selectedSq, sq);
      return;
    }
  }
  // Warn if the move leaves your own king attacked.
  if (movesIntoCheck(selectedSq, sq, byKey, youSeat)) {
    const ok = confirm("This move leaves your king in check. Confirm move?");
    if (!ok) { selectedSq = null; renderBoardOnly(); return; }
  }
  ws.send(JSON.stringify({ type: "move", from: selectedSq, to: sq }));
  selectedSq = null;
}

function movesIntoCheck(src, dst, byKey, mySeat) {
  // Simulate: apply the move on a shallow copy, check if my king is attacked.
  const sim = new Map();
  for (const [k, v] of byKey) sim.set(k, { ...v });
  const mover = sim.get(src);
  if (!mover) return false;
  sim.delete(src);
  sim.set(dst, mover);
  // Locate own king (king itself may have moved).
  let kingSq = null;
  for (const [k, v] of sim) if (v.kind === "king" && v.color === mySeat) { kingSq = k; break; }
  if (!kingSq) return false;  // no king (already captured?) — server will handle
  for (const [srcSq, pc] of sim) {
    if (pc.color === mySeat) continue;
    const dests = legalDestinations(srcSq, sim, pc.color);
    if (dests.has(kingSq)) return true;
  }
  return false;
}

function promotionPicker(from, to) {
  // Build a small overlay near the destination square.
  const rect = squareCenter(to);
  if (!rect) return;
  const wrap = boardEl.parentElement;
  const wb = wrap.getBoundingClientRect();
  const overlay = document.createElement("div");
  overlay.style.position = "fixed";
  overlay.style.left = (wb.left + rect.x) + "px";
  overlay.style.top = (wb.top + rect.y + rect.h) + "px";
  overlay.style.zIndex = "80";
  overlay.style.background = "var(--panel)";
  overlay.style.border = "1px solid var(--accent)";
  overlay.style.padding = "4px";
  overlay.style.display = "flex";
  overlay.style.gap = "3px";
  for (const k of ["queen", "rook", "bishop", "knight"]) {
    const b = document.createElement("button");
    b.style.padding = "3px 8px";
    b.textContent = PIECE_GLYPH[mySeat][k];
    b.title = k;
    b.addEventListener("click", () => {
      ws.send(JSON.stringify({ type: "move", from, to, promote: k }));
      selectedSq = null;
      overlay.remove();
      renderBoardOnly();
    });
    overlay.appendChild(b);
  }
  document.body.appendChild(overlay);
  setTimeout(() => {
    document.addEventListener("click", function clo(e) {
      if (!overlay.contains(e.target)) {
        overlay.remove();
        document.removeEventListener("click", clo);
      }
    });
  }, 0);
}

// ---- targeting state machine ----

function beginCasting(card) {
  casting = {
    slot: card.slot,
    card,
    targets: [],
    targetSpecs: targetSpecsFor(card),
    modal: null,
    sacSquares: [],
    stripSquares: [],
    discardSlots: new Set(),
    pickedKind: null,   // for piece_any
    step: 0,
    chosenOppMove: null,
    discardPicking: false,
  };
  // Cards that need a kind picked BEFORE placement:
  if (card.card_id === "piece_any") {
    // chip-row to pick kind; placement after.
    casting.awaitingKind = true;
  }
  // Cards needing modal at start:
  if (modalBeforeTargets(card.card_id)) {
    casting.awaitingModal = true;
  }
  // Special: spell_discard_draw — open hand-discard mode.
  if (card.card_id === "spell_discard_draw") {
    casting.discardPicking = true;
  }
  // Special: zero-target spells — auto-submit.
  if (casting.targetSpecs.length === 0
      && !casting.awaitingKind
      && !casting.awaitingModal
      && !casting.discardPicking
      && !needsExtraSelection(card.card_id)) {
    submitCasting();
    return;
  }
  render();
}

function cancelCasting() {
  casting = null;
  render();
}

function targetSpecsFor(card) {
  // Per CARDS.md, deterministic mapping. For dynamic modal-driven cards, we
  // derive after modal is picked.
  const id = card.card_id;
  switch (id) {
    case "piece_pawn": return ["empty_square_pawn_zone"];
    case "piece_knight": case "piece_bishop":
    case "piece_rook": case "piece_queen":
    case "piece_any":
      // piece_any zone is resolved at modal-pick time; default back-2 lets
      // the targeting machine show *something* before the modal lands.
      return ["empty_square_back2"];
    case "spell_extra_pawn_move": return ["friendly_pawn"];
    case "spell_play_2_pawns": return ["empty_square_pawn_zone", "empty_square_pawn_zone"];
    case "spell_combine_pawns":
      return ["friendly_pawn", "friendly_pawn", "friendly_pawn", "empty_square_back2"];
    case "spell_adjacent_en_passant": return ["friendly_pawn", "enemy_piece_adj"];
    case "spell_remove_pawn_draw_3": return ["friendly_pawn"];
    case "spell_pawn_to_rook": return ["friendly_pawn", "empty_square_back2"];
    case "spell_remove_minor": return ["any_knight_or_bishop"];
    case "spell_forced_promotion": return ["friendly_pawn"];
    case "spell_teleport": return ["friendly_piece", "any_empty_square"];
    case "spell_king_to_center": return ["any_king", "empty_square_central4"];
    case "spell_pawns_to_queen": return ["empty_square_back2"];
    case "spell_material_to_queen": return ["empty_square_back2"]; // + sac sub-flow
    case "spell_convert_piece": return ["enemy_piece_nonking"];
    case "spell_rook_and_minor": return ["empty_square_back2", "empty_square_back2"];
    case "spell_deploy_enemy_rank": return ["empty_square_back2_opp"];
    case "spell_quad_deploy":
      // knight, bishop, rook (back-2), then the pawn (pawn zone).
      return ["empty_square_back2", "empty_square_back2", "empty_square_back2", "empty_square_pawn_zone"];
    case "spell_queen_and_strip": return ["empty_square_back2"]; // + strip sub-flow
    case "spell_draw_rook_extra": return ["empty_square_back2"];
    // Modal-only / no targets:
    case "spell_pawn_back_or_two_moves": return [];
    case "spell_eight_or_eight": return []; // dynamic — fills after modal
    case "spell_wipe_type": return [];
    default: return [];
  }
}

function modalBeforeTargets(card_id) {
  // Cards whose modal must be picked before targets (the modal controls schema).
  return [
    "spell_pawn_back_or_two_moves",
    "spell_eight_or_eight",
    "spell_wipe_type",
  ].includes(card_id);
}

function needsExtraSelection(card_id) {
  // discard sub-flow / sac / strip / opp-picks-style modal
  return [
    "spell_material_to_queen",
    "spell_queen_and_strip",
    "spell_discard_draw",
    "spell_combine_pawns",
    "spell_rook_and_minor",
    "spell_deploy_enemy_rank",
    "piece_any",
  ].includes(card_id);
}

function currentTargetingPrompt() {
  if (!casting) return null;
  const c = casting.card;
  if (casting.awaitingKind) {
    return "pick a piece kind (chip-row at right)";
  }
  if (casting.awaitingModal) {
    return "choose a mode (right panel)";
  }
  if (casting.discardPicking) {
    return `pick cards to discard (${casting.discardSlots.size} selected) → confirm at right`;
  }
  if (c.card_id === "spell_material_to_queen") {
    const sum = casting.sacSquares.reduce((s, sq) => s + (MATERIAL_POINTS[lastBoardMap.get(sq)?.kind] || 0), 0);
    if (sum < 7) return `pick pieces to sacrifice — total ${sum}/7`;
    if (sum > 7) return `over 7 — unselect some pieces`;
    return "now pick a back-2 square to place the queen";
  }
  if (c.card_id === "spell_queen_and_strip") {
    if (!casting.targets.length) return "pick a back-2 square for the queen";
    return `pick enemy pieces to strip (≤4 mat) — total ${stripSum()}/4 → confirm at right`;
  }
  if (c.card_id === "spell_eight_or_eight") {
    if (casting.modal && casting.modal.toLowerCase().startsWith("pawn")) {
      return `pick 1..8 back-2 squares (${casting.targets.length} so far) → confirm at right`;
    }
    if (casting.modal) {
      const sum = casting.targets.reduce((s, t) => s + (MATERIAL_POINTS[t.piece_kind] || 0), 0);
      return `pick piece-kind+square pairs totaling 8 — ${sum}/8 → use right-panel chip then click square`;
    }
    return "choose mode";
  }
  const spec = casting.targetSpecs[casting.step];
  const i = casting.step + 1, n = casting.targetSpecs.length;
  switch (spec) {
    case "empty_square_back2": return `(${i}/${n}) pick a square in your back two ranks`;
    case "empty_square_back2_opp": return `(${i}/${n}) pick a square in opponent's 2nd/7th rank`;
    case "empty_square_central4": return `(${i}/${n}) pick a central square (d4/d5/e4/e5)`;
    case "any_empty_square": return `(${i}/${n}) pick any empty square`;
    case "friendly_pawn": return `(${i}/${n}) pick one of your pawns`;
    case "friendly_piece": return `(${i}/${n}) pick one of your pieces`;
    case "enemy_piece": case "enemy_piece_nonking":
    case "enemy_piece_adj": return `(${i}/${n}) pick an enemy piece`;
    case "any_knight_or_bishop": return `(${i}/${n}) pick a knight or bishop`;
    case "any_king": return `(${i}/${n}) pick a king`;
    default: return `(${i}/${n}) pick a target`;
  }
}

function validSquaresForCurrentStep(byKey) {
  if (!casting) return null;
  const set = new Set();
  const c = casting.card;
  const seat = mySeat;

  // Sub-flows that override the normal step:
  if (c.card_id === "spell_material_to_queen") {
    const sum = casting.sacSquares.reduce((s, sq) => s + (MATERIAL_POINTS[byKey.get(sq)?.kind] || 0), 0);
    if (sum !== 7) {
      // Highlight: own non-king pieces (toggle).
      for (const [sq, pc] of byKey) if (pc.color === seat && pc.kind !== "king") set.add(sq);
      return set;
    }
    // Placement step
    for (const sq of backTwoSquares(seat)) if (!byKey.has(sq)) set.add(sq);
    return set;
  }
  if (c.card_id === "spell_queen_and_strip") {
    if (casting.targets.length === 0) {
      for (const sq of backTwoSquares(seat)) if (!byKey.has(sq)) set.add(sq);
      return set;
    }
    // Strip mode: highlight enemy non-king. Toggling restricted by ≤4 sum (handled on click).
    for (const [sq, pc] of byKey) if (pc.color !== seat && pc.kind !== "king") set.add(sq);
    return set;
  }
  // piece_any: zone depends on the kind picked after the chip row.
  if (c.card_id === "piece_any" && casting.pickedKind) {
    const zone = placementZoneFor(seat, casting.pickedKind);
    for (const sq of zone) if (!byKey.has(sq)) set.add(sq);
    return set;
  }
  if (c.card_id === "spell_eight_or_eight" && casting.modal) {
    const mode = casting.modal.toLowerCase();
    if (mode.startsWith("pawn")) {
      const used = new Set(casting.targets.map(t => t.square));
      for (const sq of pawnZoneSquares(seat)) if (!byKey.has(sq) && !used.has(sq)) set.add(sq);
      return set;
    }
    if (mode.startsWith("material")) {
      if (!casting.pickedKind) return set;  // wait for chip click
      const used = new Set(casting.targets.map(t => t.square));
      for (const sq of backTwoSquares(seat)) if (!byKey.has(sq) && !used.has(sq)) set.add(sq);
      return set;
    }
  }

  // Standard step-by-step
  const spec = casting.targetSpecs[casting.step];
  if (!spec) return set;
  const exclude = new Set();
  for (const t of casting.targets) if (t && t.square) exclude.add(t.square);

  switch (spec) {
    case "empty_square_back2":
      for (const sq of backTwoSquares(seat)) if (!byKey.has(sq) && !exclude.has(sq)) set.add(sq);
      break;
    case "empty_square_pawn_zone":
      for (const sq of pawnZoneSquares(seat)) if (!byKey.has(sq) && !exclude.has(sq)) set.add(sq);
      break;
    case "empty_square_back2_opp":
      for (const sq of oppRankSquares(seat)) if (!byKey.has(sq) && !exclude.has(sq)) set.add(sq);
      break;
    case "empty_square_central4":
      for (const sq of ["d4","d5","e4","e5"]) if (!byKey.has(sq)) set.add(sq);
      break;
    case "any_empty_square":
      for (let f = 0; f < 8; f++) for (let r = 0; r < 8; r++) {
        const sq = frToSq(f, r); if (!byKey.has(sq)) set.add(sq);
      }
      break;
    case "friendly_pawn": {
      // exclude already-picked pawns (for combine_pawns 3-pick)
      const picked = new Set(casting.targets.slice(0, casting.step).map(t => t.square));
      for (const [sq, pc] of byKey) {
        if (pc.color === seat && pc.kind === "pawn" && !picked.has(sq)) set.add(sq);
      }
      break;
    }
    case "friendly_piece":
      for (const [sq, pc] of byKey) if (pc.color === seat) set.add(sq);
      break;
    case "enemy_piece":
      for (const [sq, pc] of byKey) if (pc.color !== seat) set.add(sq);
      break;
    case "enemy_piece_nonking":
      for (const [sq, pc] of byKey) if (pc.color !== seat && pc.kind !== "king") set.add(sq);
      break;
    case "enemy_piece_adj": {
      // adjacent to the pawn picked in step 0
      const pawnSq = casting.targets[0]?.square;
      if (!pawnSq) break;
      const [pf, pr] = sqToFR(pawnSq);
      for (let df = -1; df <= 1; df++) for (let dr = -1; dr <= 1; dr++) {
        if (!df && !dr) continue;
        const nf = pf + df, nr = pr + dr;
        if (nf < 0 || nf > 7 || nr < 0 || nr > 7) continue;
        const nsq = frToSq(nf, nr);
        const pc = byKey.get(nsq);
        if (pc && pc.color !== seat) set.add(nsq);
      }
      break;
    }
    case "any_knight_or_bishop":
      for (const [sq, pc] of byKey) if (pc.kind === "knight" || pc.kind === "bishop") set.add(sq);
      break;
    case "any_king":
      for (const [sq, pc] of byKey) if (pc.kind === "king") set.add(sq);
      break;
  }
  return set;
}

function stripSum() {
  return (casting.stripSquares || []).reduce((s, sq) => s + (MATERIAL_POINTS[lastBoardMap.get(sq)?.kind] || 0), 0);
}

function handleTargetClick(sq) {
  const c = casting.card;

  // If a modal / kind picker is pending, force the user to resolve it
  // before they can interact with the board. Avoids the previous race
  // where clicks could advance the step machine past a still-pending
  // modal and leave the card unresolvable.
  if (casting.awaitingModal) {
    flashStatusErr("pick a mode first (right panel)");
    return;
  }
  if (casting.awaitingKind) {
    flashStatusErr("pick a piece kind first (right panel)");
    return;
  }

  // material_to_queen — toggle sac selections, then place
  if (c.card_id === "spell_material_to_queen") {
    const sum = casting.sacSquares.reduce((s, sq2) => s + (MATERIAL_POINTS[lastBoardMap.get(sq2)?.kind] || 0), 0);
    if (sum !== 7) {
      const pc = lastBoardMap.get(sq);
      if (!pc || pc.color !== mySeat || pc.kind === "king") return;
      const i = casting.sacSquares.indexOf(sq);
      if (i >= 0) casting.sacSquares.splice(i, 1);
      else casting.sacSquares.push(sq);
      render();
      return;
    }
    // sum is 7 — placement
    if (!backTwoSquares(mySeat).includes(sq) || lastBoardMap.has(sq)) return;
    casting.targets = [{ square: sq, sac_squares: casting.sacSquares.slice() }];
    submitCasting();
    return;
  }

  if (c.card_id === "spell_queen_and_strip") {
    if (casting.targets.length === 0) {
      if (!backTwoSquares(mySeat).includes(sq) || lastBoardMap.has(sq)) return;
      casting.targets = [{ square: sq, strip_squares: [] }];
      render();
      return;
    }
    // strip toggling
    const pc = lastBoardMap.get(sq);
    if (!pc || pc.color === mySeat || pc.kind === "king") return;
    const i = casting.stripSquares.indexOf(sq);
    if (i >= 0) casting.stripSquares.splice(i, 1);
    else {
      const val = MATERIAL_POINTS[pc.kind] || 0;
      if (stripSum() + val > 4) { flashStatusErr("over 4 material"); return; }
      casting.stripSquares.push(sq);
    }
    render();
    return;
  }

  if (c.card_id === "spell_eight_or_eight") {
    if (!casting.modal) return;
    const mode = casting.modal.toLowerCase();
    if (mode.startsWith("pawn")) {
      const i = casting.targets.findIndex(t => t.square === sq);
      if (i >= 0) casting.targets.splice(i, 1);
      else {
        if (casting.targets.length >= 8) return;
        if (!pawnZoneSquares(mySeat).includes(sq) || lastBoardMap.has(sq)) return;
        casting.targets.push({ square: sq });
      }
      render();
      return;
    }
    if (mode.startsWith("material")) {
      if (!casting.pickedKind) { flashStatusErr("pick a piece kind first (chip-row)"); return; }
      if (!backTwoSquares(mySeat).includes(sq) || lastBoardMap.has(sq)) return;
      const sum = casting.targets.reduce((s, t) => s + (MATERIAL_POINTS[t.piece_kind] || 0), 0);
      const v = MATERIAL_POINTS[casting.pickedKind] || 0;
      if (sum + v > 8) { flashStatusErr("placing this would exceed 8"); return; }
      casting.targets.push({ piece_kind: casting.pickedKind, square: sq });
      casting.pickedKind = null;
      render();
      return;
    }
  }

  // Standard step
  const valid = validSquaresForCurrentStep(lastBoardMap);
  if (!valid || !valid.has(sq)) {
    flashStatusErr("invalid target");
    return;
  }
  casting.targets.push({ square: sq });
  casting.step += 1;
  // After step, may need modal injection (e.g. combine_pawns -> modal -> back2)
  maybeInjectModalAfterStep();
  if (casting.step >= casting.targetSpecs.length && !casting.awaitingModal && !casting.awaitingKindMidway) {
    submitCasting();
  } else {
    render();
  }
}

function maybeInjectModalAfterStep() {
  const c = casting.card;
  // combine_pawns: after 3 pawns picked, ask Knight/Bishop modal before placement.
  if (c.card_id === "spell_combine_pawns" && casting.step === 3 && casting.modal === null) {
    casting.awaitingModal = true;
  }
  if (c.card_id === "spell_rook_and_minor" && casting.step === 2 && casting.modal === null) {
    casting.awaitingModal = true;
  }
  if (c.card_id === "spell_deploy_enemy_rank" && casting.step === 1 && casting.modal === null) {
    casting.awaitingModal = true;
  }
  // Forced Promotion: opponent picks the Knight/Bishop — no caster modal.
}

function submitCasting() {
  if (!ws || ws.readyState !== 1) return;
  const c = casting.card;
  const payload = { type: "play_card", slot: c.slot };
  let targets = casting.targets.slice();
  // discard_subset: pack slots as the single target.
  if (c.card_id === "spell_discard_draw") {
    targets = [{ slots: [...casting.discardSlots] }];
  }
  payload.targets = targets;
  if (casting.modal !== null) payload.modal = casting.modal;
  // piece_any: modal is the chosen kind (capitalized).
  if (c.card_id === "piece_any" && casting.pickedKind) {
    payload.modal = capitalize(casting.pickedKind);
  }
  // king_to_center: schema is "any_piece_kind","empty_square_central4" — but
  // the engine actually wants squares. We collected (king_sq, dest_sq) above
  // already as {square:...} pairs. Good.
  ws.send(JSON.stringify(payload));
  casting = null;
}

function capitalize(s) { return (s || "").charAt(0).toUpperCase() + s.slice(1); }

// ---- prompt-area rendering (server prompts + targeting helpers) ----

function renderPromptArea() {
  promptArea.innerHTML = "";

  // 1. Server-side prompt (choose_opp_move, mulligan-style etc).
  if (serverPrompt) {
    renderServerPrompt(serverPrompt);
    return;
  }

  // 2. Client-side casting flow widgets (chip-row, modal, sub-confirm).
  if (casting) {
    renderCastingWidgets();
    return;
  }

  const div = document.createElement("div");
  div.className = "side-empty";
  div.textContent = "no active prompt.";
  promptArea.appendChild(div);
}

function renderServerPrompt(p) {
  const box = document.createElement("div");
  box.className = "prompt-box";
  const lbl = document.createElement("div");
  lbl.className = "label";
  lbl.textContent = p.label || p.kind;
  box.appendChild(lbl);

  if (p.kind === "choose_opp_move") {
    const list = document.createElement("div");
    list.className = "moves-list";
    for (const m of (p.moves || [])) {
      const b = document.createElement("button");
      b.textContent = `${m.from} → ${m.to}` + (m.promote ? ` =${m.promote[0].toUpperCase()}` : "");
      b.addEventListener("click", () => {
        ws.send(JSON.stringify({
          type: "prompt_response", prompt_id: p.prompt_id,
          move: m,
        }));
        // Server also accepts direct `move` for the chooser path; either way
        // clear the prompt locally to avoid double-submission UX.
        serverPrompt = null;
        renderPromptArea();
      });
      list.appendChild(b);
    }
    box.appendChild(list);
  } else if (p.kind === "pick_opp_hand") {
    const list = document.createElement("div");
    list.className = "moves-list";
    for (const c of (p.opp_hand || [])) {
      const b = document.createElement("button");
      b.textContent = `${c.cost}g · ${c.name}`;
      b.addEventListener("click", () => {
        ws.send(JSON.stringify({
          type: "prompt_response", prompt_id: p.prompt_id, opp_slot: c.slot,
        }));
        serverPrompt = null;
        renderPromptArea();
      });
      list.appendChild(b);
    }
    if (!p.opp_hand || !p.opp_hand.length) {
      const m = document.createElement("div");
      m.className = "progress";
      m.textContent = "opponent's hand is empty";
      box.appendChild(m);
    }
    box.appendChild(list);
  } else if (p.kind === "select_modal") {
    const opts = document.createElement("div");
    opts.className = "opts";
    for (const o of (p.options || [])) {
      const b = document.createElement("button");
      b.textContent = o;
      b.addEventListener("click", () => {
        ws.send(JSON.stringify({
          type: "prompt_response", prompt_id: p.prompt_id, option: o,
        }));
        serverPrompt = null;
        renderPromptArea();
      });
      opts.appendChild(b);
    }
    box.appendChild(opts);
  } else {
    const txt = document.createElement("div");
    txt.className = "progress";
    txt.textContent = "(use the board to answer)";
    box.appendChild(txt);
  }

  const cancel = document.createElement("div");
  cancel.className = "row";
  const cb = document.createElement("button");
  cb.textContent = "cancel";
  cb.addEventListener("click", () => {
    ws.send(JSON.stringify({ type: "cancel_prompt", prompt_id: p.prompt_id }));
    serverPrompt = null;
    renderPromptArea();
  });
  cancel.appendChild(cb);
  box.appendChild(cancel);
  promptArea.appendChild(box);
}

function renderCastingWidgets() {
  const c = casting.card;
  const box = document.createElement("div");
  box.className = "prompt-box";

  const lbl = document.createElement("div");
  lbl.className = "label";
  lbl.textContent = c.name;
  box.appendChild(lbl);

  const prog = document.createElement("div");
  prog.className = "progress";
  prog.textContent = currentTargetingPrompt() || "";
  box.appendChild(prog);

  // 1. piece_any: pick a kind first.
  if (c.card_id === "piece_any" && casting.awaitingKind) {
    const opts = document.createElement("div");
    opts.className = "opts";
    for (const [kind, cost] of Object.entries(PIECE_COSTS)) {
      const b = document.createElement("button");
      b.textContent = `${capitalize(kind)} (${cost}g)`;
      b.addEventListener("click", () => {
        casting.pickedKind = kind;
        casting.awaitingKind = false;
        render();
      });
      opts.appendChild(b);
    }
    box.appendChild(opts);
  }

  // 2. Modal (cards w/ modal at start, or injected after step)
  if (casting.awaitingModal) {
    const opts = document.createElement("div");
    opts.className = "opts";
    const options = modalOptionsForCard(c.card_id);
    for (const o of options) {
      const b = document.createElement("button");
      b.textContent = o;
      b.addEventListener("click", () => {
        casting.modal = o;
        casting.awaitingModal = false;
        // spell_eight_or_eight has variable-count targets filled in
        // AFTER the modal mode is picked. Don't auto-submit — let the
        // player pick squares.
        if (c.card_id === "spell_eight_or_eight") {
          render();
          return;
        }
        // If all targets are already collected, the modal was the last
        // missing piece — submit now. (Catches the case where the user
        // clicked the placement square before picking the modal, e.g.
        // combine_pawns: 3 pawns + back-2 square chosen, modal pending.)
        if (casting.step >= casting.targetSpecs.length
            && !casting.discardPicking
            && !casting.awaitingKind) {
          submitCasting();
          return;
        }
        render();
      });
      opts.appendChild(b);
    }
    box.appendChild(opts);
  }

  // 3. eight_or_eight material-mode chip row for piece kind picker.
  if (c.card_id === "spell_eight_or_eight" && casting.modal
      && casting.modal.toLowerCase().startsWith("material")) {
    const sum = casting.targets.reduce((s, t) => s + (MATERIAL_POINTS[t.piece_kind] || 0), 0);
    const sumEl = document.createElement("div");
    sumEl.className = "sum" + (sum === 8 ? " ok" : "");
    sumEl.textContent = `total ${sum}/8`;
    box.appendChild(sumEl);
    const opts = document.createElement("div");
    opts.className = "opts";
    for (const k of ["knight", "bishop", "rook", "queen"]) {
      const b = document.createElement("button");
      b.textContent = `${capitalize(k)} (${MATERIAL_POINTS[k]})`;
      if (sum + MATERIAL_POINTS[k] > 8) b.disabled = true;
      if (casting.pickedKind === k) b.classList.add("primary");
      b.addEventListener("click", () => {
        casting.pickedKind = k;
        render();
      });
      opts.appendChild(b);
    }
    box.appendChild(opts);
    if (sum === 8) {
      const row = document.createElement("div");
      row.className = "row";
      const confirm = document.createElement("button");
      confirm.className = "primary";
      confirm.textContent = "confirm";
      confirm.addEventListener("click", submitCasting);
      row.appendChild(confirm);
      box.appendChild(row);
    }
  }

  // 4. eight_or_eight pawn-mode confirm
  if (c.card_id === "spell_eight_or_eight" && casting.modal
      && casting.modal.toLowerCase().startsWith("pawn")) {
    const row = document.createElement("div");
    row.className = "row";
    const confirm = document.createElement("button");
    confirm.className = "primary";
    confirm.textContent = `confirm (${casting.targets.length})`;
    confirm.disabled = casting.targets.length < 1;
    confirm.addEventListener("click", submitCasting);
    row.appendChild(confirm);
    box.appendChild(row);
  }

  // 5. material_to_queen sub-flow sum
  if (c.card_id === "spell_material_to_queen") {
    const sum = casting.sacSquares.reduce((s, sq) => s + (MATERIAL_POINTS[lastBoardMap.get(sq)?.kind] || 0), 0);
    const sumEl = document.createElement("div");
    sumEl.className = "sum" + (sum === 7 ? " ok" : "");
    sumEl.textContent = `sacrifice ${sum}/7`;
    box.appendChild(sumEl);
  }

  // 6. queen_and_strip sub-flow
  if (c.card_id === "spell_queen_and_strip" && casting.targets.length >= 1) {
    const sum = stripSum();
    const sumEl = document.createElement("div");
    sumEl.className = "sum" + (sum <= 4 ? " ok" : "");
    sumEl.textContent = `strip ${sum}/4`;
    box.appendChild(sumEl);
    const row = document.createElement("div");
    row.className = "row";
    const confirm = document.createElement("button");
    confirm.className = "primary";
    confirm.textContent = "confirm";
    confirm.addEventListener("click", () => {
      casting.targets[0].strip_squares = casting.stripSquares.slice();
      submitCasting();
    });
    row.appendChild(confirm);
    box.appendChild(row);
  }

  // 7. discard_subset confirm
  if (c.card_id === "spell_discard_draw" && casting.discardPicking) {
    const row = document.createElement("div");
    row.className = "row";
    const confirm = document.createElement("button");
    confirm.className = "primary";
    confirm.textContent = `confirm discard (${casting.discardSlots.size})`;
    confirm.disabled = false; // 0 is legal (waste card)
    confirm.addEventListener("click", () => submitCasting());
    row.appendChild(confirm);
    box.appendChild(row);
  }

  // Cancel row
  const cancelRow = document.createElement("div");
  cancelRow.className = "row";
  const cb = document.createElement("button");
  cb.textContent = "cancel";
  cb.addEventListener("click", cancelCasting);
  cancelRow.appendChild(cb);
  box.appendChild(cancelRow);

  promptArea.appendChild(box);
}

function modalOptionsForCard(card_id) {
  switch (card_id) {
    case "spell_pawn_back_or_two_moves": return ["Backward", "Two pawns"];
    case "spell_combine_pawns": return ["Knight", "Bishop"];
    case "spell_rook_and_minor": return ["Knight", "Bishop"];
    case "spell_deploy_enemy_rank": return ["Pawn", "Rook"];
    case "spell_eight_or_eight": return ["Pawns", "Material"];
    case "spell_wipe_type": return ["Pawn", "Knight", "Bishop", "Rook", "Queen"];
    case "spell_forced_promotion": return ["Knight", "Bishop"];
    default: return [];
  }
}

// ---- timer ----

function updateTimer(deadlineMs) {
  if (timerHandle) { clearInterval(timerHandle); timerHandle = null; }
  if (!deadlineMs) {
    timerDots.innerHTML = "";
    timerNum.textContent = "—";
    return;
  }
  function tick() {
    const left = Math.max(0, Math.ceil((deadlineMs - Date.now()) / 1000));
    timerNum.textContent = left + "s";
    // 5 dots, each 18s. Drain from right.
    const filled = Math.min(5, Math.ceil(left / 18));
    const low = left <= 18;
    timerDots.innerHTML = "";
    for (let i = 0; i < 5; i++) {
      const d = document.createElement("span");
      d.className = "timer-dot" + (i < filled ? "" : " empty");
      if (i === 0 && low && filled === 1) d.classList.add("low");
      timerDots.appendChild(d);
    }
  }
  tick();
  timerHandle = setInterval(tick, 500);
}

// ---- errors / toasts ----

function flashStatusErr(text) {
  statusLine.textContent = text;
  statusLine.classList.add("err");
  statusLine.classList.remove("target");
  if (statusErrTimer) clearTimeout(statusErrTimer);
  statusErrTimer = setTimeout(() => {
    statusLine.classList.remove("err");
    statusErrTimer = null;
    if (lastState) renderStatusLine(lastState, mySeat);
  }, 2200);
}

function toast(text) {
  let el = document.getElementById("toast");
  if (!el) {
    el = document.createElement("div");
    el.id = "toast";
    document.body.appendChild(el);
  }
  el.textContent = text;
  el.hidden = false;
  if (toastTimer) clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.remove(); toastTimer = null; }, 1600);
}

function escapeHtml(s) {
  return (s || "").replace(/[&<>"']/g, c => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#39;"
  }[c]));
}
