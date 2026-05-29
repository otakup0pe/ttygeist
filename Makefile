VENV := .venv
UV := uv
PYTHON := $(VENV)/bin/python
PYTEST := $(VENV)/bin/pytest
RUFF := $(VENV)/bin/ruff

.PHONY: test test-verbose lint lint-fix clean help

$(VENV)/.dev-installed: pyproject.toml
	$(UV) venv $(VENV) --python 3.10 --clear
	$(UV) pip install -e ".[dev]"
	@touch $@

test: $(VENV)/.dev-installed lint
	$(PYTEST) tests/ -q --timeout=10

lint: $(VENV)/.dev-installed
	$(RUFF) check ttygeist/ tests/
	$(RUFF) format --check ttygeist/ tests/

lint-fix: $(VENV)/.dev-installed
	$(RUFF) check --fix ttygeist/ tests/
	$(RUFF) format ttygeist/ tests/

clean:
	rm -rf $(VENV) .pytest_cache __pycache__ ttygeist/__pycache__ tests/__pycache__
	rm -f $(VENV)/.dev-installed
