const lobby = document.getElementById("lobby");
const roomSection = document.getElementById("room");
const roomCodeEl = document.getElementById("room-code");
const nameInput = document.getElementById("player-name");
const joinCode = document.getElementById("join-code");
const dealBtn = document.getElementById("deal-btn");
const leaveBtn = document.getElementById("leave-btn");
const statusEl = document.getElementById("room-status");
const tableEl = document.getElementById("table");
const deckEl = document.getElementById("deck");

const SUIT_GLYPH = { S: "♠", H: "♥", D: "♦", C: "♣" };
const RED_SUITS = new Set(["H", "D"]);
const POSITIONS = ["bottom", "left", "top", "right"];
const ROUND_DELAY = 65;   // ms between cards
const FLIGHT_MS = 260;    // per-card flight duration

let ws = null;
let mySeat = null;
let players = [];

document.getElementById("create-btn").addEventListener("click", async () => {
  const r = await fetch("/api/rooms", { method: "POST" });
  if (!r.ok) return alert("could not create room");
  const { code } = await r.json();
  joinCode.value = code;
  if (!nameInput.value.trim()) nameInput.focus();
});

document.getElementById("join-btn").addEventListener("click", () => {
  const code = joinCode.value.trim().toUpperCase();
  const name = nameInput.value.trim() || "anon";
  if (code.length !== 4) return alert("enter a 4-letter code");
  connect(code, name);
});

dealBtn.addEventListener("click", () => {
  if (ws && ws.readyState === 1) ws.send(JSON.stringify({ type: "deal" }));
});

leaveBtn.addEventListener("click", () => {
  if (ws) try { ws.close(); } catch {}
  resetRoom();
  roomSection.hidden = true;
  lobby.hidden = false;
});

function connect(code, name) {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  ws = new WebSocket(`${proto}://${location.host}/ws/${code}`);

  ws.addEventListener("open", () => {
    ws.send(JSON.stringify({ name }));
    lobby.hidden = true;
    roomSection.hidden = false;
    roomCodeEl.textContent = code;
    resetRoom();
  });

  ws.addEventListener("message", (e) => {
    const msg = JSON.parse(e.data);
    if (msg.type === "welcome") {
      mySeat = msg.your_seat;
    } else if (msg.type === "state") {
      players = msg.room.players;
      renderSeats();
      dealBtn.disabled = players.length !== 4 || msg.room.dealt;
      statusEl.textContent =
        players.length < 4
          ? `waiting for ${4 - players.length} more player${4 - players.length === 1 ? "" : "s"}...`
          : msg.room.dealt
          ? ""
          : "ready to deal";
    } else if (msg.type === "deal") {
      statusEl.textContent = "";
      animateDeal(msg);
    }
  });

  ws.addEventListener("close", (e) => {
    statusEl.textContent = `disconnected${e.reason ? `: ${e.reason}` : ""}`;
    dealBtn.disabled = true;
  });
}

function resetRoom() {
  mySeat = null;
  players = [];
  for (const seat of document.querySelectorAll(".seat")) {
    seat.querySelector(".name").textContent = "";
    seat.querySelector(".hand").innerHTML = "";
    seat.classList.remove("you");
  }
  // Clean any in-flight cards from a previous deal
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

function animateDeal({ dealer, your_seat, hands }) {
  mySeat = your_seat;
  // Clear prior hands but keep names/seat structure
  for (const seat of document.querySelectorAll(".seat .hand")) seat.innerHTML = "";
  for (const c of tableEl.querySelectorAll(".card.flying")) c.remove();

  // Per-seat in-flight counter so we know each card's destination slot
  const dealt = [0, 0, 0, 0];

  let step = 0;
  for (let round = 0; round < 13; round++) {
    for (let r = 0; r < 4; r++) {
      const seat = (dealer + 1 + r) % 4;
      const card = hands[seat][round];
      const seatPos = seatPosition(seat);
      const targetIdx = dealt[seat]++;
      setTimeout(() => flyCard(seatPos, card, targetIdx, seat), step * ROUND_DELAY);
      step++;
    }
  }
}

function flyCard(seatPos, card, targetIdx, seatIdx) {
  const handEl = tableEl.querySelector(`.seat.${seatPos} .hand`);
  const isYou = seatIdx === mySeat;

  // Create the destination card (final resting state) so layout reserves a slot
  const finalCard = document.createElement("div");
  finalCard.className = "card";
  if (isYou && !card.hidden) {
    finalCard.classList.add("face");
    if (RED_SUITS.has(card.suit)) finalCard.classList.add("red");
    finalCard.innerHTML = `<span class="rank">${card.rank}</span><span class="suit">${SUIT_GLYPH[card.suit]}</span>`;
  }
  finalCard.style.visibility = "hidden";
  handEl.appendChild(finalCard);

  // Compute target rect after layout, then animate a separate "flying" clone
  const targetRect = finalCard.getBoundingClientRect();
  const tableRect = tableEl.getBoundingClientRect();
  const deckRect = deckEl.getBoundingClientRect();

  const fly = finalCard.cloneNode(true);
  fly.classList.add("flying");
  fly.style.visibility = "visible";
  fly.style.left = `${deckRect.left - tableRect.left}px`;
  fly.style.top = `${deckRect.top - tableRect.top}px`;
  fly.style.transition = `transform ${FLIGHT_MS}ms cubic-bezier(.2,.7,.2,1)`;
  fly.style.transform = "translate(0,0)";
  tableEl.appendChild(fly);

  const dx = targetRect.left - deckRect.left;
  const dy = targetRect.top - deckRect.top;

  requestAnimationFrame(() => {
    fly.style.transform = `translate(${dx}px, ${dy}px)`;
  });

  fly.addEventListener("transitionend", () => {
    finalCard.style.visibility = "visible";
    fly.remove();
  }, { once: true });
}
