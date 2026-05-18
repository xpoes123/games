const entry = document.getElementById("entry");
const game = document.getElementById("game");
const nameInput = document.getElementById("player-name");
const joinBtn = document.getElementById("join-btn");
const dealBtn = document.getElementById("deal-btn");
const leaveBtn = document.getElementById("leave-btn");
const muteBtn = document.getElementById("mute-btn");
const entryStatus = document.getElementById("entry-status");
const seatStatus = document.getElementById("seat-status");
const tableEl = document.getElementById("table");
const deckEl = document.getElementById("deck");
const bidArea = document.getElementById("bid-area");
const bidInfo = document.getElementById("bid-info");
const bidHistoryEl = document.getElementById("bid-history");
const bidPanel = document.getElementById("bid-panel");
const bidLevelSel = document.getElementById("bid-level");
const bidStrainSel = document.getElementById("bid-strain");
const bidSubmitBtn = document.getElementById("bid-submit");
const bidPassBtn = document.getElementById("bid-pass");
const bidError = document.getElementById("bid-error");
const callPanel = document.getElementById("call-panel");
const callRankSel = document.getElementById("call-rank");
const callSuitSel = document.getElementById("call-suit");
const callSubmitBtn = document.getElementById("call-submit");
const callError = document.getElementById("call-error");

const SUIT_GLYPH = { S: "♠", H: "♥", D: "♦", C: "♣" };
const POSITIONS = ["bottom", "left", "top", "right"];
const ROUND_DELAY = 65;
const FLIGHT_MS = 280;

let ws = null;
let mySeat = null;
let players = [];
let tricksWon = [0, 0, 0, 0];
let lastState = null;
let iAmPartner = false;   // private: true if server told me I'm the partner
let lastWasMyTurn = false; // for turn-sound edge detection

let muted = localStorage.getItem("games-muted") === "1";
function updateMuteBtn() { muteBtn.textContent = muted ? "unmute" : "mute"; }
updateMuteBtn();
muteBtn.addEventListener("click", () => {
  muted = !muted;
  localStorage.setItem("games-muted", muted ? "1" : "0");
  updateMuteBtn();
});

let audioCtx = null;
function getAudioCtx() {
  if (!audioCtx) {
    const AC = window.AudioContext || window.webkitAudioContext;
    if (!AC) return null;
    audioCtx = new AC();
  }
  if (audioCtx.state === "suspended") audioCtx.resume();
  return audioCtx;
}

function playTurnBeep() {
  if (muted) return;
  try {
    const ctx = getAudioCtx();
    if (!ctx) return;
    const t0 = ctx.currentTime;

    // A single soft sine tone in the mid-low range. Slow attack so it
    // doesn't startle, long gentle release for a wind-chime feel. A
    // quieter octave layer underneath adds warmth without brightness.
    const layer = (freq, gain) => {
      const osc = ctx.createOscillator();
      const g = ctx.createGain();
      osc.type = "sine";
      osc.frequency.setValueAtTime(freq, t0);
      osc.connect(g).connect(ctx.destination);
      g.gain.setValueAtTime(0.0001, t0);
      // ~80ms linear-ish attack — no sharp transient
      g.gain.exponentialRampToValueAtTime(gain, t0 + 0.08);
      // Hold briefly, then long gentle release
      g.gain.setValueAtTime(gain, t0 + 0.18);
      g.gain.exponentialRampToValueAtTime(0.0001, t0 + 1.2);
      osc.start(t0);
      osc.stop(t0 + 1.3);
    };

    layer(440.00, 0.035);  // A4 — primary
    layer(220.00, 0.01);   // A3 — subtle octave below for warmth
  } catch {
    // Ignore audio errors (autoplay policy, etc.)
  }
}

function isMyTurn() {
  if (!lastState || mySeat === null) return false;
  if (lastState.phase === "bidding") return lastState.current_bidder === mySeat;
  if (lastState.phase === "calling") return lastState.declarer === mySeat;
  if (lastState.phase === "playing") return lastState.turn === mySeat;
  return false;
}

function maybePlayTurnSound() {
  const now = isMyTurn();
  if (now && !lastWasMyTurn) playTurnBeep();
  lastWasMyTurn = now;
}

const STRAIN_LABEL = { NT: "NT", C: "♣", D: "♦", H: "♥", S: "♠" };
const STRAIN_CLASS = {
  NT: "strain-NT", S: "strain-S", H: "strain-H", D: "strain-D", C: "strain-C",
};

function setStatus(msg) { entryStatus.textContent = msg; }

joinBtn.addEventListener("click", () => {
  const name = nameInput.value.trim() || "anon";
  setStatus("connecting...");
  joinBtn.disabled = true;
  connect(name);
});

nameInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter") joinBtn.click();
});

dealBtn.addEventListener("click", () => {
  if (ws && ws.readyState === 1) ws.send(JSON.stringify({ type: "deal" }));
});

leaveBtn.addEventListener("click", () => {
  if (ws) try { ws.close(); } catch {}
  showEntry();
});

function showEntry() {
  game.hidden = true;
  entry.hidden = false;
  joinBtn.disabled = false;
  setStatus("");
  resetSeats();
}

function showGame() {
  entry.hidden = true;
  game.hidden = false;
}

function connect(name) {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  // Resolve "ws" relative to the current page so this works whether
  // we're served at /bridge/ or any other future mount point.
  const wsUrl = new URL("ws", location.href);
  wsUrl.protocol = proto + ":";
  ws = new WebSocket(wsUrl);

  ws.addEventListener("open", () => {
    ws.send(JSON.stringify({ name }));
    showGame();
  });

  ws.addEventListener("message", (e) => {
    const msg = JSON.parse(e.data);
    if (msg.type === "welcome") {
      mySeat = msg.your_seat;
    } else if (msg.type === "error") {
      const text = msg.message || "error";
      // Route the error into whichever panel is currently visible. During
      // PLAYING phase neither panel is visible — flash the status line.
      if (!callPanel.hidden) callError.textContent = text;
      else if (!bidPanel.hidden) bidError.textContent = text;
      else flashStatusError(text);
    } else if (msg.type === "you_are_partner") {
      iAmPartner = true;
      renderPhase();
    } else if (msg.type === "partner_called") {
      // The state broadcast that follows will carry partner_card; nothing
      // extra to do here for opponents. Hook left for a future "card called"
      // animation if we want one.
    } else if (msg.type === "state") {
      lastState = msg.table;
      players = msg.table.players;
      tricksWon = msg.table.tricks_won || [0, 0, 0, 0];
      renderSeats();   // arrow indicator depends on lastState; render after assignment
      renderPhase();
      renderBidHistory();
      maybePlayTurnSound();
    } else if (msg.type === "deal") {
      tricksWon = [0, 0, 0, 0];
      iAmPartner = false;
      renderSeats();
      clearPlayArea();
      animateDeal(msg).then(() => {
        setTimeout(() => {
          sortMyHand();
          // No status text here — renderPhase (driven by state messages) owns
          // the seat-status line now.
          renderPhase();
        }, SORT_DELAY);
      });
    } else if (msg.type === "play") {
      animatePlay(msg.seat, msg.card);
    } else if (msg.type === "trick_won") {
      handleTrickWon(msg.winner, msg.tricks_won);
    }
  });

  ws.addEventListener("close", (e) => {
    setStatus(`disconnected${e.reason ? `: ${e.reason}` : ""}`);
    joinBtn.disabled = false;
    if (!game.hidden) {
      // We were in the game; bounce back to entry on disconnect
      game.hidden = true;
      entry.hidden = false;
    }
  });

  ws.addEventListener("error", () => {
    setStatus("connection error");
    joinBtn.disabled = false;
  });
}

function resetSeats() {
  mySeat = null;
  players = [];
  lastState = null;
  iAmPartner = false;
  for (const seat of document.querySelectorAll(".seat")) {
    seat.querySelector(".name").textContent = "";
    seat.querySelector(".hand").innerHTML = "";
    seat.classList.remove("you");
  }
  for (const c of tableEl.querySelectorAll(".card.flying")) c.remove();
  bidArea.hidden = true;
  bidPanel.hidden = true;
  callPanel.hidden = true;
  bidError.textContent = "";
  callError.textContent = "";
}

function formatBid(bid) {
  if (!bid) return "—";
  const cls = STRAIN_CLASS[bid.strain] || "";
  return `${bid.level}<span class="${cls}">${STRAIN_LABEL[bid.strain]}</span>`;
}

function seatName(idx) {
  const p = players[idx];
  return p ? p.name : `seat ${idx}`;
}

function renderPhase() {
  if (!lastState) return;
  const phase = lastState.phase;

  if (phase === "lobby") {
    seatStatus.textContent = `${players.length}/4 seated`;
    dealBtn.disabled = players.length === 0;
    bidArea.hidden = true;
    return;
  }

  if (phase === "bidding") {
    dealBtn.disabled = true;
    bidArea.hidden = false;
    const cur = lastState.current_bidder;
    const curBid = lastState.current_bid;
    const bidder = cur === null ? "—" : seatName(cur);
    const youTurn = cur === mySeat;
    const youTag = youTurn ? `<span class="yours">your turn</span> · ` : "";
    const curTag = curBid
      ? `current: <span class="current">${formatBid(curBid)}</span> by ${seatName(curBid.seat)}`
      : "no bid yet";
    seatStatus.textContent = `bidding · ${youTurn ? "your turn" : bidder + "'s turn"}`;
    bidInfo.innerHTML = `${youTag}${curTag}`;
    bidPanel.hidden = !youTurn;
    if (youTurn) bidPassBtn.disabled = false;
    return;
  }

  if (phase === "calling") {
    dealBtn.disabled = true;
    bidArea.hidden = false;
    bidPanel.hidden = true;
    const contract = lastState.contract;
    const declarer = lastState.declarer;
    const isDeclarer = mySeat === declarer;
    seatStatus.textContent = "calling partner";
    bidInfo.innerHTML = isDeclarer
      ? `contract: <span class="current">${formatBid(contract)}</span> · call a card you don't hold`
      : `contract: <span class="current">${formatBid(contract)}</span> by ${seatName(declarer)} · waiting for partner call...`;
    callPanel.hidden = !isDeclarer;
    callError.textContent = "";
    return;
  }

  if (phase === "playing") {
    bidArea.hidden = false;
    bidPanel.hidden = true;
    callPanel.hidden = true;
    const contract = lastState.contract;
    const declarer = lastState.declarer;
    const partnerCard = lastState.partner_card;
    seatStatus.textContent = "playing";
    let info = `contract: <span class="current">${formatBid(contract)}</span> by ${seatName(declarer)}`;
    if (partnerCard) {
      const cls = STRAIN_CLASS[partnerCard.suit] || "";
      info += ` · partner card: ${partnerCard.rank}<span class="${cls}">${SUIT_GLYPH[partnerCard.suit]}</span>`;
    }
    if (iAmPartner) info += ` · <span class="yours">you are partner</span>`;
    bidInfo.innerHTML = info;
    dealBtn.disabled = true;
    return;
  }

  if (phase === "done") {
    bidArea.hidden = false;
    bidPanel.hidden = true;
    callPanel.hidden = true;
    const contract = lastState.contract;
    const declarer = lastState.declarer;
    const partnerSeat = lastState.partner_seat;
    const partnerCard = lastState.partner_card;
    const result = lastState.result;
    const totals = lastState.total_tricks || [0, 0, 0, 0];
    const handsPlayed = lastState.hands_played || 0;
    seatStatus.textContent = `hand ${handsPlayed} complete · deal to continue`;
    let info = `<span class="current">${formatBid(contract)}</span> by ${seatName(declarer)}`;
    if (partnerSeat !== null && partnerSeat !== undefined) {
      info += ` & <span class="current">${seatName(partnerSeat)}</span>`;
    }
    if (partnerCard) {
      const cls = STRAIN_CLASS[partnerCard.suit] || "";
      info += ` (called ${partnerCard.rank}<span class="${cls}">${SUIT_GLYPH[partnerCard.suit]}</span>)`;
    }
    if (result) {
      const verdict = result.made
        ? `<span class="made">made ${result.delta === 0 ? "" : `+${result.delta}`}</span>`
        : `<span class="down">down ${-result.delta}</span>`;
      info += ` · ${verdict} (${result.team_tricks}/${result.target})`;
    }
    // Append cumulative totals
    if (handsPlayed > 0) {
      const totalsStr = [0, 1, 2, 3]
        .map((i) => `${seatName(i)} ${totals[i]}`)
        .join(" · ");
      info += `<br/><span class="totals">totals: ${totalsStr}</span>`;
    }
    bidInfo.innerHTML = info;
    dealBtn.disabled = false;
    return;
  }
}

let statusErrorTimer = null;
function flashStatusError(text) {
  seatStatus.textContent = text;
  seatStatus.classList.add("err");
  if (statusErrorTimer) clearTimeout(statusErrorTimer);
  statusErrorTimer = setTimeout(() => {
    seatStatus.classList.remove("err");
    renderPhase();
    statusErrorTimer = null;
  }, 2000);
}

function reconcilePlayArea() {
  // Authoritative source: lastState.current_trick. Clear flying clones
  // (which may be orphaned by paused animations) and force each play slot
  // to match the server's view.
  if (!lastState) return;
  for (const fly of tableEl.querySelectorAll(".card.flying")) fly.remove();
  const expected = new Map();
  for (const entry of lastState.current_trick || []) {
    const pos = seatPosition(entry.seat);
    if (pos) expected.set(pos, entry.card);
  }
  for (const slot of tableEl.querySelectorAll(".play-slot")) {
    const pos = slot.dataset.pos;
    const want = expected.get(pos);
    slot.innerHTML = "";
    if (want) slot.appendChild(makeCardEl(want, true));
  }
}

document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible") reconcilePlayArea();
});

function renderBidHistory() {
  if (!lastState) { bidHistoryEl.innerHTML = ""; return; }
  const history = lastState.bid_history || [];
  // Hide auto-passes from empty seats — they clutter the history during
  // solo/partial-table testing and don't add information.
  const items = history.filter((e) => !e.auto).map((e) => {
    const name = `<span class="name">${seatName(e.seat)}</span>`;
    if (e.kind === "pass") return `<span class="item">${name}: pass</span>`;
    if (e.kind === "stuck") {
      const cls = STRAIN_CLASS[e.strain] || "";
      return `<span class="item">${name}: stuck ${e.level}<span class="${cls}">${STRAIN_LABEL[e.strain]}</span></span>`;
    }
    if (e.kind === "bid") {
      const cls = STRAIN_CLASS[e.strain] || "";
      return `<span class="item">${name}: ${e.level}<span class="${cls}">${STRAIN_LABEL[e.strain]}</span></span>`;
    }
    return "";
  });
  bidHistoryEl.innerHTML = items.join("");
}

callSubmitBtn.addEventListener("click", () => {
  if (!ws || ws.readyState !== 1) return;
  callError.textContent = "";
  const rank = callRankSel.value;
  const suit = callSuitSel.value;
  ws.send(JSON.stringify({ type: "call_partner", rank, suit }));
});

bidSubmitBtn.addEventListener("click", () => {
  if (!ws || ws.readyState !== 1) return;
  bidError.textContent = "";
  const level = parseInt(bidLevelSel.value, 10);
  const strain = bidStrainSel.value;
  ws.send(JSON.stringify({ type: "bid", level, strain }));
});

bidPassBtn.addEventListener("click", () => {
  if (!ws || ws.readyState !== 1) return;
  bidError.textContent = "";
  ws.send(JSON.stringify({ type: "pass" }));
});

function seatPosition(seatIdx) {
  if (mySeat === null) return null;
  return POSITIONS[(seatIdx - mySeat + 4) % 4];
}

function activeSeat() {
  if (!lastState) return null;
  if (lastState.phase === "bidding") return lastState.current_bidder;
  if (lastState.phase === "calling") return lastState.declarer;
  if (lastState.phase === "playing") return lastState.turn;
  return null;
}

function renderSeats() {
  const active = activeSeat();
  for (let i = 0; i < 4; i++) {
    const pos = seatPosition(i);
    if (!pos) continue;
    const seatEl = tableEl.querySelector(`.seat.${pos}`);
    const p = players[i];
    seatEl.classList.toggle("you", i === mySeat);
    seatEl.classList.toggle("turn", i === active);
    const t = tricksWon[i] || 0;
    const name = p ? p.name : "—";
    seatEl.querySelector(".name").textContent = t > 0 ? `${name} · ${t}` : name;
  }
}

function slotOffset(seatPos, idx) {
  // Returns { left, top } in pixels within the seat's hand container.
  // Strides chosen so 13 cards fit in the configured hand dimensions.
  const STRIDE_H = 18;       // top seat
  const STRIDE_V = 16;       // left/right
  const MY_STRIDE = 22;      // bottom (you)
  if (seatPos === "bottom") return { left: idx * MY_STRIDE, top: 0 };
  if (seatPos === "top")    return { left: idx * STRIDE_H, top: 0 };
  if (seatPos === "left")   return { left: 0, top: idx * STRIDE_V };
  if (seatPos === "right")  return { left: 0, top: idx * STRIDE_V };
}

function makeCardEl(card, faceUp) {
  const el = document.createElement("div");
  el.className = "card";
  if (faceUp && !card.hidden) {
    el.classList.add("face");
    el.dataset.rank = card.rank;
    el.dataset.suit = card.suit;
    el.innerHTML = `<span class="rank">${card.rank}</span><span class="suit">${SUIT_GLYPH[card.suit]}</span>`;
  }
  return el;
}

function makeMyCardEl(card) {
  const el = makeCardEl(card, true);
  el.addEventListener("click", () => playCard(card));
  return el;
}

function playCard(card) {
  if (!ws || ws.readyState !== 1) return;
  ws.send(JSON.stringify({ type: "play", rank: card.rank, suit: card.suit }));
}

function clearPlayArea() {
  for (const slot of tableEl.querySelectorAll(".play-slot")) slot.innerHTML = "";
}

const RANK_ORDER = { "2": 0, "3": 1, "4": 2, "5": 3, "6": 4, "7": 5, "8": 6, "9": 7, "T": 8, "J": 9, "Q": 10, "K": 11, "A": 12 };
const SUIT_ORDER = { S: 0, H: 1, D: 2, C: 3 };
const MY_STRIDE = 22;
const SORT_MS = 380;
const SORT_DELAY = 300;     // beat after dealing before the resort kicks in
const SORT_STAGGER = 18;    // stagger per card so the resort feels alive

function sortMyHand() {
  const handEl = document.querySelector(".seat.bottom .hand");
  const cards = [...handEl.children];
  cards.sort((a, b) => {
    const sa = SUIT_ORDER[a.dataset.suit];
    const sb = SUIT_ORDER[b.dataset.suit];
    if (sa !== sb) return sa - sb;
    return RANK_ORDER[b.dataset.rank] - RANK_ORDER[a.dataset.rank]; // A high → descending
  });
  cards.forEach((card, newIdx) => {
    const newLeft = newIdx * MY_STRIDE;
    const oldLeft = parseFloat(card.style.left) || 0;
    card.style.left = `${newLeft}px`;
    card.style.zIndex = String(newIdx); // later suits sit on top of earlier
    if (oldLeft === newLeft) return;
    card.animate(
      [
        { transform: `translateX(${oldLeft - newLeft}px)` },
        { transform: "translateX(0)" },
      ],
      { duration: SORT_MS, easing: "cubic-bezier(.2,.7,.2,1)", delay: newIdx * SORT_STAGGER },
    );
  });
}

async function animateDeal({ dealer, your_seat, hands }) {
  mySeat = your_seat;
  for (const h of document.querySelectorAll(".seat .hand")) h.innerHTML = "";
  for (const c of tableEl.querySelectorAll(".card.flying")) c.remove();

  const seatDealt = [0, 0, 0, 0];
  const animations = [];

  let step = 0;
  for (let round = 0; round < 13; round++) {
    for (let r = 0; r < 4; r++) {
      const seat = (dealer + 1 + r) % 4;
      const card = hands[seat][round];
      const slot = seatDealt[seat]++;
      animations.push(
        new Promise((resolve) => {
          setTimeout(() => flyCard(seat, card, slot).then(resolve), step * ROUND_DELAY);
        })
      );
      step++;
    }
  }
  await Promise.all(animations);
}

function repackHand(seatPos) {
  // For non-bottom seats: re-position remaining cards to fill the gap after a play.
  const handEl = tableEl.querySelector(`.seat.${seatPos} .hand`);
  const cards = [...handEl.children];
  cards.forEach((card, idx) => {
    const off = slotOffset(seatPos, idx);
    const oldLeft = parseFloat(card.style.left) || 0;
    const oldTop = parseFloat(card.style.top) || 0;
    if (oldLeft === off.left && oldTop === off.top) return;
    card.style.left = `${off.left}px`;
    card.style.top = `${off.top}px`;
    card.animate(
      [
        { transform: `translate(${oldLeft - off.left}px, ${oldTop - off.top}px)` },
        { transform: "translate(0, 0)" },
      ],
      { duration: 240, easing: "ease-out" },
    );
  });
}

const PLAY_MS = 320;
const TRICK_HOLD_MS = 900;     // how long the completed trick stays visible
const TRICK_COLLECT_MS = 480;

async function handleTrickWon(winnerSeat, newTricks) {
  // Hold first so the 4th play animation (~320ms) has time to land in its
  // slot. If we snapshotted before the hold the most recent card would
  // still be in flight and get orphaned when its siblings fly to the winner.
  await new Promise((r) => setTimeout(r, TRICK_HOLD_MS));

  const slotCards = [];
  for (const slot of tableEl.querySelectorAll(".play-slot")) {
    const card = slot.firstElementChild;
    if (card) slotCards.push({ slot, card });
  }
  if (slotCards.length === 0) {
    tricksWon = newTricks;
    renderSeats();
    return;
  }

  const winnerPos = seatPosition(winnerSeat);
  if (!winnerPos) {
    // Nothing to animate toward — just clear and update
    for (const { slot } of slotCards) slot.innerHTML = "";
    tricksWon = newTricks;
    renderSeats();
    return;
  }

  const tableRect = tableEl.getBoundingClientRect();
  const winnerEl = tableEl.querySelector(`.seat.${winnerPos}`);
  const wRect = winnerEl.getBoundingClientRect();
  const targetX = wRect.left - tableRect.left + wRect.width / 2 - 14;
  const targetY = wRect.top - tableRect.top + wRect.height / 2 - 20;

  const animations = [];
  for (const { slot, card } of slotCards) {
    const cardRect = card.getBoundingClientRect();
    const startX = cardRect.left - tableRect.left;
    const startY = cardRect.top - tableRect.top;
    const fly = card.cloneNode(true);
    fly.classList.add("flying");
    fly.style.left = `${startX}px`;
    fly.style.top = `${startY}px`;
    fly.style.width = "var(--card-w)";
    fly.style.height = "var(--card-h)";
    tableEl.appendChild(fly);
    slot.innerHTML = "";

    const anim = fly.animate(
      [
        { transform: "translate(0,0) scale(1)", opacity: 1 },
        {
          transform: `translate(${targetX - startX}px, ${targetY - startY}px) scale(0.45)`,
          opacity: 0.1,
        },
      ],
      { duration: TRICK_COLLECT_MS, easing: "cubic-bezier(.4,0,.2,1)", fill: "forwards" },
    );
    animations.push(anim.finished.then(() => fly.remove()).catch(() => fly.remove()));
  }

  await Promise.all(animations);
  tricksWon = newTricks;
  renderSeats();
}

function animatePlay(seatIdx, card) {
  const seatPos = seatPosition(seatIdx);
  if (!seatPos) return;
  const handEl = tableEl.querySelector(`.seat.${seatPos} .hand`);
  const isYou = seatIdx === mySeat;

  // Source: the specific card for "you", any (last) card for other seats
  let sourceEl;
  if (isYou) {
    sourceEl = handEl.querySelector(`[data-rank="${card.rank}"][data-suit="${card.suit}"]`);
  } else {
    sourceEl = handEl.lastElementChild;
  }
  if (!sourceEl) return;

  const tableRect = tableEl.getBoundingClientRect();
  const sourceRect = sourceEl.getBoundingClientRect();
  const startX = sourceRect.left - tableRect.left;
  const startY = sourceRect.top - tableRect.top;

  const slot = tableEl.querySelector(`.play-slot[data-pos="${seatPos}"]`);
  const slotRect = slot.getBoundingClientRect();
  const endX = slotRect.left - tableRect.left;
  const endY = slotRect.top - tableRect.top;

  // Remove the source from the hand and re-pack remaining cards
  sourceEl.remove();
  if (isYou) sortMyHand(); else repackHand(seatPos);

  // Flying clone (face-up — the played card is revealed for everyone)
  const fly = makeCardEl(card, true);
  fly.classList.add("flying");
  fly.style.left = `${startX}px`;
  fly.style.top = `${startY}px`;
  // Standardise played-card size regardless of source seat
  fly.style.width = "var(--card-w)";
  fly.style.height = "var(--card-h)";
  tableEl.appendChild(fly);

  const anim = fly.animate(
    [
      { transform: "translate(0, 0)" },
      { transform: `translate(${endX - startX}px, ${endY - startY}px)` },
    ],
    { duration: PLAY_MS, easing: "cubic-bezier(.2,.7,.2,1)" },
  );

  anim.finished.then(() => {
    const landed = makeCardEl(card, true);
    slot.innerHTML = "";  // one card per slot — most recent wins
    slot.appendChild(landed);
    fly.remove();
  }).catch(() => fly.remove());
}

function flyCard(seatIdx, card, slot) {
  const seatPos = seatPosition(seatIdx);
  const seatEl = tableEl.querySelector(`.seat.${seatPos}`);
  const handEl = seatEl.querySelector(".hand");
  const isYou = seatIdx === mySeat;

  // Final card in its destination slot (initially hidden so position is known)
  const target = isYou ? makeMyCardEl(card) : makeCardEl(card, false);
  const offset = slotOffset(seatPos, slot);
  target.style.left = `${offset.left}px`;
  target.style.top = `${offset.top}px`;
  target.style.visibility = "hidden";
  handEl.appendChild(target);

  // Compute table-space coordinates for both start (deck) and end (target)
  const tableRect = tableEl.getBoundingClientRect();
  const deckRect = deckEl.getBoundingClientRect();
  const targetRect = target.getBoundingClientRect();

  const startX = deckRect.left - tableRect.left;
  const startY = deckRect.top - tableRect.top;
  const endX = targetRect.left - tableRect.left;
  const endY = targetRect.top - tableRect.top;

  // Flying clone, absolutely positioned in the table coordinate space
  const fly = makeCardEl(card, isYou);
  fly.classList.add("flying");
  fly.style.left = `${startX}px`;
  fly.style.top = `${startY}px`;
  // Match the destination card size — for "you" seat the card is larger
  if (isYou) {
    fly.style.width = "var(--my-card-w)";
    fly.style.height = "var(--my-card-h)";
  }
  tableEl.appendChild(fly);

  const anim = fly.animate(
    [
      { transform: "translate(0, 0)" },
      { transform: `translate(${endX - startX}px, ${endY - startY}px)` },
    ],
    { duration: FLIGHT_MS, easing: "cubic-bezier(.2,.7,.2,1)", fill: "forwards" },
  );

  return anim.finished.then(() => {
    target.style.visibility = "visible";
    fly.remove();
  }).catch(() => {
    target.style.visibility = "visible";
    fly.remove();
  });
}
