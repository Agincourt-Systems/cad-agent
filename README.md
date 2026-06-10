# CAD Agent Harness

`cadx` is a local CAD-as-code harness for coding agents. It lets an agent edit
ordinary `build123d` Python files, run them, collect CAD artifacts, inspect
spatial facts, render deterministic visual summaries, and evaluate requirement
checks with minimal human input.

The first implementation is intentionally CLI-first. MCP and richer browser
viewer integrations can wrap the same run artifacts once the local contract is
stable.

## Quick Start

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e .[cad,render,test]
cadx init
cadx run design.py --params params.yaml
cadx inspect artifacts/runs/0001
cadx render artifacts/runs/0001
cadx evaluate artifacts/runs/0001 --requirements requirements.yaml
```

If `build123d` is not installed, `cadx run` still starts and reports a clear
dependency error when the design source imports `build123d`.

## Artifact Contract

Each successful run creates:

- `source_snapshot.py`
- `params.resolved.yaml`
- CAD exports when the runtime supports them
- `spatial.json`
- `diagnostics.json`
- `checks.json` after evaluation
- `views/contact.png` after rendering

The harness is designed so text-only agents can reason from JSON and
multimodal agents can inspect the rendered contact sheet.
