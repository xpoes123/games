# games

Online multiplayer card games at **[games.djiang.xyz](https://games.djiang.xyz)**.

First game: a bridge variant for 4 players. Spec pending.

## Quick start (local)

```bash
python -m venv venv
source venv/bin/activate
pip install -e ".[dev]"
python -m src.main
# http://127.0.0.1:7781
```

Create a room, share the 4-letter code with friends, they paste it in to join.
No accounts — pick any name on the join screen.

## Tests

```bash
pytest tests/
```

## Deploy

See [`deploy/`](./deploy) for the systemd unit and reverse-proxy snippets,
and [`CLAUDE.md`](./CLAUDE.md) for the one-time VPS install steps.
