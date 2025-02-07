.RECIPEPREFIX := >
.DEFAULT_GOAL := help

PYTHON ?= python
PYPI_URL := "https://pypi.org/simple"
PYTHON_COMPATIBLE := $(shell "$(PYTHON)" -c "import sys; print(sys.version_info >= (3, 8))" 2>/dev/null || echo False)
VENV := venv

.PHONY: help
help:
>   @echo "Pywssocks: A SOCKS proxy implementation over WebSocket protocol."
>   @echo "\nUsage: make <subcommand>"
>   @echo "Subcommands:"
>   @echo "  install    - Create a Python virtual environment (venv) and install pywssocks in it"
>   @echo "  develop    - Create a venv and install pywssocks with development packages in it"
>   @echo "  server/forward  - Run pywssocks server in forward proxy mode"
>   @echo "  server/reverse  - Run pywssocks server in reverse proxy mode"
>   @echo "  client/forward  - Run pywssocks client in forward proxy mode"
>   @echo "  client/reverse  - Run pywssocks client in reverse proxy mode"
>   @echo "  docs       - Start the documentation server using mkdocs"
>   @echo "  test       - Run tests with pytest"
>   @echo "  test/cli   - Run cli tests with pytest (with all features)"
>   @echo "  test/lib   - Run lib tests with pytest"
>   @echo "  lint       - Format code using black"
>   @echo "  version    - Bump patch version (0.0.x)"
>   @echo "  version/minor - Bump minor version (0.x.0)"
>   @echo "  version/major - Bump major version (x.0.0)"

venv:
>   @if [ "$(PYTHON_COMPATIBLE)" = "False" ]; then \
>       echo "Error: Python 3.8 or higher is required."; \
>       exit 1; \
>   fi
>   @[ ! -d "$(VENV)" ] && "$(PYTHON)" -m venv "$(VENV)" && echo "Info: Virtual environment created." || :

install:
>   @[ ! -d "$(VENV)" ] && $(MAKE) venv || :
>   @"$(VENV)/bin/python" -m pip install -i "$(PYPI_URL)" -U pip && \
>   "$(VENV)/bin/python" -m pip install -i "$(PYPI_URL)" -e . \
>   && echo "Info: Successfully installed Pywssocks in "$(VENV)"." \
>   || echo "Error: Failed to install Pywssocks."

develop:
>   "$(VENV)/bin/python" -m pip install -i "$(PYPI_URL)" -e .[dev]

.PHONY: server/forward
server/forward: install
>   @"$(VENV)/bin/pywssocks" server -t example_token

.PHONY: server/reverse
server/reverse: install
>   @"$(VENV)/bin/pywssocks" server -t example_token -p 1080 -r

.PHONY: client/forward
client/forward: install
>   @"$(VENV)/bin/pywssocks" client -t example_token -u ws://localhost:8765 -p 1080

.PHONY: client/reverse
client/reverse: install
>   @"$(VENV)/bin/pywssocks" client -t example_token -u ws://localhost:8765 -r

.PHONY: docs
docs: develop
>   @"$(VENV)/bin/mkdocs" serve

.PHONY: test
test: develop
>   @"$(VENV)/bin/pytest" -n auto

.PHONY: test/lib
test/lib: develop
>   @"$(VENV)/bin/pytest" tests/test_lib.py -n auto

.PHONY: test/cli
test/cli: develop
>   @"$(VENV)/bin/pytest" --cli-features tests/test_cli.py -n auto

.PHONY: lint
lint: develop
>   @"$(VENV)/bin/black" pywssocks tests

.PHONY: version
version: develop
>   @"$(VENV)/bin/bump2version" patch

.PHONY: version/minor
version/minor: develop
>   @"$(VENV)/bin/bump2version" minor

.PHONY: version/major
version/major: develop
>   @"$(VENV)/bin/bump2version" major