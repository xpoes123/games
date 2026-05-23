// Past-games index. Fetches /chess/api/games, renders a table. Toggle for
// "just mine" filters by the localStorage client id used during play.

const CLIENT_ID_KEY = "chess_client_id";
const tableHost = document.getElementById("table-host");
const countEl = document.getElementById("count");
const allBtn = document.getElementById("filter-all");
const mineBtn = document.getElementById("filter-mine");

const myId = localStorage.getItem(CLIENT_ID_KEY) || null;
if (!myId) mineBtn.disabled = true;

function fmtTs(epoch) {
  if (!epoch) return "—";
  const d = new Date(epoch * 1000);
  const today = new Date();
  const same = d.toDateString() === today.toDateString();
  if (same) return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  return d.toLocaleDateString([], { month: "short", day: "numeric" });
}

function fmtDuration(start, end) {
  if (!start || !end) return "—";
  const s = end - start;
  if (s < 60) return s + "s";
  const m = Math.floor(s / 60);
  return m + "m" + (s % 60 ? " " + (s % 60) + "s" : "");
}

function renderGames(rows) {
  countEl.textContent = `(${rows.length})`;
  if (rows.length === 0) {
    tableHost.innerHTML = `<div class="empty">no games yet — play one and it'll land here.</div>`;
    return;
  }
  const headers = `<tr>
    <th>code</th><th>white</th><th>black</th><th>winner</th>
    <th>reason</th><th>length</th><th>when</th>
  </tr>`;
  const body = rows.map(r => {
    const winnerCls = r.winner === "white" ? "w" : r.winner === "black" ? "b" : "";
    const winnerName = r.winner === "white" ? r.white_name
      : r.winner === "black" ? r.black_name
      : "—";
    const meTag = (r.white_player_id === myId || r.black_player_id === myId) ? " ★" : "";
    return `<tr>
      <td class="slug"><a href="/chess/replay/${r.slug}">${r.slug}</a>${meTag}</td>
      <td>${escapeHtml(r.white_name)}</td>
      <td>${escapeHtml(r.black_name)}</td>
      <td class="winner ${winnerCls}">${escapeHtml(winnerName)}</td>
      <td>${escapeHtml(r.win_reason || "—")}</td>
      <td>${fmtDuration(r.started_at, r.ended_at)}</td>
      <td>${fmtTs(r.ended_at)}</td>
    </tr>`;
  }).join("");
  tableHost.innerHTML = `<table>${headers}${body}</table>`;
}

function escapeHtml(s) {
  return (s ?? "").replace(/[&<>"']/g, c => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

async function load(player_id) {
  const url = new URL("/chess/api/games", location.origin);
  if (player_id) url.searchParams.set("player_id", player_id);
  const res = await fetch(url);
  const data = await res.json();
  renderGames(data.games || []);
}

allBtn.addEventListener("click", () => {
  allBtn.classList.add("active"); mineBtn.classList.remove("active");
  load(null);
});
mineBtn.addEventListener("click", () => {
  if (!myId) return;
  mineBtn.classList.add("active"); allBtn.classList.remove("active");
  load(myId);
});

load(null);
