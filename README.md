# OPC Platform

## Installation

```bash
# Using uv (recommended)
uv sync

# Or using pip
pip install -e .
# Or: pip install -r requirements.txt
```

## Quick Start

```bash
python3 opc.py --json init
python3 opc.py --json opc create --id gzh-curator --name GzhCuratorOpc --from-template gzh-curator
python3 opc.py --json catalog list
python3 opc.py --json scenario run --opc gzh-curator --scenario weekly-topic-batch --input ./input.json
python3 opc.py --json scenario run --opc gzh-curator --scenario weekly-topic-batch --input ./input.json --source-data-dir ./path/to/scraped-data
python3 opc.py --json run watch --run <run_id>
python3 opc.py --json publish trigger --run <run_id>
python3 opc.py --json graph view --opc gzh-curator --scenario weekly-topic-batch
python3 opc.py --json graph review --opc gzh-curator --scenario weekly-topic-batch --node PublisherAgent --comment "发布前增加人工确认节点"
```

## Web Frontend (React + Vite)

```bash
cd web
npm install
npm run build
cd ..
python3 opc.py web serve --host 127.0.0.1 --port 8787
```

Open: `http://127.0.0.1:8787`

Web includes:
- OPC overview dashboard
- Run center (trigger publish)
- Decision center (approve)
- Graph review board
- Execute panel (run scenario with form)

## Run Tests

```bash
# Install dev dependencies first: uv sync --extra dev  or  pip install -e ".[dev]"
python3 -m pytest tests/ -v
```

## Notes

- Workspace metadata is under `/.opc` in current directory.
- IDs used in paths are strictly validated (rejects `..`, `/`, `\\`).
- JSON state files use atomic write (temporary file + rename).
- Integrations (paths configurable via OPC manifest or env):
  - gzh-scraper: scrapes 公众号素材；manifest `integrations.gzh_scraper_root` 或 env `OPC_GZH_SCRAPER_ROOT`
  - copublisher: publishes to draft box；manifest `integrations.copublisher_root` 或 env `OPC_COPUBLISHER_ROOT`
- Scenario execution supports `--execute-integrations` to invoke external commands.
- If your公众号素材已经抓取完成，可用 `--source-data-dir` 跳过 `gzh-scraper` 抓取步骤。
- Quality/effect agents use Cursor Agent CLI by default (`use_cursor_agent=true`).
- If Cursor Agent asks for workspace trust, execute once interactively or keep default `cursor_agent_trust=true`.
- You can tune quality-agent timeout with `cursor_agent_timeout_sec` in inputs (default `90` seconds).
- Strict mode: if quality/effect agents fail or return empty output, run status becomes `failed` (no mock fallback content).
- `input.json` example:

```json
{
  "objective": "作为土木工程师拆解职场升职能力",
  "reference_accounts": ["刘润", "职场知行先锋", "栩先生", "MBA智库"],
  "target_account": "职场螺丝刀",
  "cursor_agent_timeout_sec": 5
}
```

