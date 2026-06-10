# MEMORY

- Parent instructions in `/home/orb/code/AGENTS.md` require strict TDD, ADRs
  before implementation, thorough comments, and disciplined commits.
- Push completed changes to `origin` at `git@github.com:torchhound/cad-agent.git`.
- `fjord` lacks `python3-venv`; CAD dependencies are installed in the Python
  3.10 user site with `python3 -m pip install --user -e '.[cad,render,test]'`.
