const entry = document.getElementById("entry");
const game = document.getElementById("game");
const nameInput = document.getElementById("player-name");
const joinBtn = document.getElementById("join-btn");
const dealBtn = document.getElementById("deal-btn");
const leaveBtn = document.getElementById("leave-btn");
const entryStatus = document.getElementById("entry-status");
const seatStatus = document.getElementById("seat-status");
const tableEl = document.getElementById("table");
const deckEl = document.getElementById("deck");

const SUIT_GLYPH = { S: "♠", H: "♥", D: "♦", C: "♣" };
const RED_SUITS = new Set(["H", "D"]);
const POSITIONS = ["bottom", "left", "top", "right"];
const ROUND_DELAY = 65;
const FLIGHT_MS = 280;

let ws = null;
let mySeat = null;
let players = [];

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
  ws = new WebSocket(`${proto}://${location.host}/ws`);

  ws.addEventListener("open", () => {
    ws.send(JSON.stringify({ name }));
    showGame();
  });

  ws.addEventListener("message", (e) => {
    const msg = JSON.parse(e.data);
    if (msg.type === "welcome") {
      mySeat = msg.your_seat;
    } else if (msg.type === "state") {
      players = msg.table.players;
      renderSeats();
      const remaining = 4 - players.length;
      seatStatus.textContent = remaining > 0
        ? `waiting for ${remaining} more player${remaining === 1 ? "" : "s"}...`
        : msg.table.dealt ? "dealt" : "ready to deal";
      dealBtn.disabled = remaining > 0 || msg.table.dealt;
    } else if (msg.type === "deal") {
      seatStatus.textContent = "dealing...";
      animateDeal(msg).then(() => { seatStatus.textContent = "dealt"; });
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
  for (const seat of document.querySelectorAll(".seat")) {
    seat.querySelector(".name").textContent = "";
    seat.querySelector(".hand").innerHTML = "";
    seat.classList.remove("you");
  }
  for (const c of tableEl.querySelectorAll(".card.flying")) c.remove();
}

function seatPosition(seatIdx) {
  if (mySeat === null) return null;
  return POSITIONS[(seatIdx - mySeat + 4) % 4];
}

function renderSeats() {
  for (let i = 0; i < 4; i++) {
    const pos = seatPosition(i);
    if (!pos) continue;
    const seatEl = tableEl.querySelector(`.seat.${pos}`);
    const p = players[i];
    seatEl.classList.toggle("you", i === mySeat);
    seatEl.querySelector(".name").textContent = p ? p.name : "—";
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
    if (RED_SUITS.has(card.suit)) el.classList.add("red");
    el.innerHTML = `<span class="rank">${card.rank}</span><span class="suit">${SUIT_GLYPH[card.suit]}</span>`;
  }
  return el;
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

function flyCard(seatIdx, card, slot) {
  const seatPos = seatPosition(seatIdx);
  const seatEl = tableEl.querySelector(`.seat.${seatPos}`);
  const handEl = seatEl.querySelector(".hand");
  const isYou = seatIdx === mySeat;

  // Final card in its destination slot (initially hidden so position is known)
  const target = makeCardEl(card, isYou);
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
