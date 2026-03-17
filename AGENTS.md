# AGENTS.md

## Cursor Cloud specific instructions

### Project overview

OPC Platform is a multi-agent orchestration platform for solo entrepreneurs (单人公司). It has two components:

- **Python backend** (CLI + HTTP server) in `opc_platform/` and `org/`
- **React/Vite frontend** in `web/`

### Running services

See `README.md` for standard commands. Non-obvious notes:

- The backend serves the built frontend as static files from `web/dist/`. You must run `npm run build` in `web/` before starting the server, or the dashboard shows a "not built" fallback page.
- The web server is started with `python opc.py web serve --host 127.0.0.1 --port 8787`. Before using the web UI, initialize the workspace with `python opc.py --json init` and create at least one OPC with `python opc.py --json opc create --id gzh-curator --name GzhCuratorOpc --from-template gzh-curator`.
- All state is file-based under `.opc/` in the working directory — no database required.
- The virtual environment is at `.venv/`; use `.venv/bin/python` to run commands, or activate it.

### Testing

- Python: `.venv/bin/python -m pytest tests/ -v` — all tests are self-contained (use temp dirs, no external services).
- Frontend type check: `cd web && npx tsc -b`

### External integrations (optional)

Cursor Agent CLI, gzh-scraper, and copublisher are external tools not included in this repo. Tests run without them (`use_cursor_agent: False`).
