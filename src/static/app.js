const lobby = document.getElementById("lobby");
const roomSection = document.getElementById("room");
const roomCodeEl = document.getElementById("room-code");
const playersEl = document.getElementById("players");
const chatLog = document.getElementById("chat-log");
const chatForm = document.getElementById("chat-form");
const chatInput = document.getElementById("chat-input");
const nameInput = document.getElementById("player-name");
const joinCode = document.getElementById("join-code");

let ws = null;

document.getElementById("create-btn").addEventListener("click", async () => {
  const r = await fetch("/api/rooms", { method: "POST" });
  if (!r.ok) return alert("could not create room");
  const { code } = await r.json();
  joinCode.value = code;
  if (!nameInput.value.trim()) nameInput.focus();
});

document.getElementById("join-btn").addEventListener("click", () => {
  const code = joinCode.value.trim().toUpperCase();
  const name = nameInput.value.trim() || "Player";
  if (code.length !== 4) return alert("enter a 4-letter code");
  connect(code, name);
});

function connect(code, name) {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  ws = new WebSocket(`${proto}://${location.host}/ws/${code}`);

  ws.addEventListener("open", () => {
    ws.send(JSON.stringify({ name }));
    lobby.hidden = true;
    roomSection.hidden = false;
    roomCodeEl.textContent = code;
  });

  ws.addEventListener("message", (e) => {
    const msg = JSON.parse(e.data);
    if (msg.type === "state") renderState(msg.room);
    else if (msg.type === "chat") appendChat(msg.from, msg.body);
  });

  ws.addEventListener("close", (e) => {
    appendChat("system", `disconnected${e.reason ? `: ${e.reason}` : ""}`);
  });
}

function renderState(room) {
  playersEl.innerHTML = "";
  for (const p of room.players) {
    const li = document.createElement("li");
    li.textContent = p.name;
    playersEl.appendChild(li);
  }
}

function appendChat(from, body) {
  if (!body) return;
  const line = document.createElement("div");
  line.className = from === "system" ? "line system" : "line";
  const f = document.createElement("span");
  f.className = "from";
  f.textContent = from;
  line.appendChild(f);
  line.appendChild(document.createTextNode(body));
  chatLog.appendChild(line);
  chatLog.scrollTop = chatLog.scrollHeight;
}

document.getElementById("leave-btn").addEventListener("click", () => {
  if (ws) try { ws.close(); } catch {}
  roomSection.hidden = true;
  lobby.hidden = false;
  playersEl.innerHTML = "";
  chatLog.innerHTML = "";
});

chatForm.addEventListener("submit", (e) => {
  e.preventDefault();
  if (!ws || ws.readyState !== 1) return;
  const body = chatInput.value.trim();
  if (!body) return;
  ws.send(JSON.stringify({ body }));
  chatInput.value = "";
});
