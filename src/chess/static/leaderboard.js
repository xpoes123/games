const CLIENT_ID_KEY = "chess_client_id";
const host = document.getElementById("host");
const myId = localStorage.getItem(CLIENT_ID_KEY);

function escapeHtml(s) {
  return (s ?? "").replace(/[&<>"']/g, c =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

async function load() {
  const res = await fetch("/chess/api/leaderboard");
  const data = await res.json();
  const rows = data.players || [];
  if (!rows.length) {
    host.innerHTML = `<div class="empty">no rated players yet — play 3 games to land here.</div>`;
    return;
  }
  const head = `<tr><th>#</th><th>handle</th><th>rating</th>
    <th>W</th><th>L</th><th>games</th></tr>`;
  const body = rows.map((r, i) => {
    const youCls = (r.client_id === myId) ? " class=\"you\"" : "";
    return `<tr${youCls}>
      <td class="rank">${i + 1}</td>
      <td class="handle">${escapeHtml(r.handle)}</td>
      <td class="rating">${r.rating}</td>
      <td>${r.wins}</td>
      <td>${r.losses}</td>
      <td>${r.games_played}</td>
    </tr>`;
  }).join("");
  host.innerHTML = `<table>${head}${body}</table>`;
}

load();
