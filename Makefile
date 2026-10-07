# DeskWidgets: common tasks
#   make run          run from the source tree (no install)
#   make install      install for the current user into ~/.local
#   make deb          build dist/deskwidgets_<version>_all.deb
#   make check        syntax checks (Python, Lua, JSON, shell, GNOME extension)
#   make previews     render every widget into build/previews (all sizes)
#   make screenshots  regenerate docs/screenshots

PYTHON ?= /usr/bin/python3
VERSION := $(shell sed -n "s/^VERSION = '\(.*\)'/\1/p" deskwidgets/__init__.py)

.PHONY: run install uninstall deb check previews screenshots clean

run:
	./bin/deskwidgets

install:
	./install.sh

uninstall:
	./install.sh --uninstall

deb:
	./packaging/build-deb.sh

check:
	$(PYTHON) -m py_compile deskwidgets/*.py bin/deskwidgets widgets/*/*.py
	@for f in widgets/*/widget.json; do $(PYTHON) -c "import json,sys; json.load(open(sys.argv[1]))" $$f || exit 1; done
	@if command -v luac5.4 >/dev/null; then for f in engine/*.lua widgets/*/*.lua; do luac5.4 -p $$f || exit 1; done; \
	 elif command -v luac >/dev/null; then for f in engine/*.lua widgets/*/*.lua; do luac -p $$f || exit 1; done; \
	 else echo "luac not found, Lua syntax not checked"; fi
	@for f in install.sh packaging/*.sh widgets/*/*.sh; do \
	  if head -1 $$f | grep -q bash; then bash -n $$f || exit 1; else sh -n $$f || exit 1; fi; done
	@if command -v node >/dev/null; then for f in gnome-extension/*/extension.js; do \
	  node --check --input-type=module < $$f || exit 1; done; fi
	@echo "all good"

previews:
	@mkdir -p build/previews
	@for d in widgets/*/; do t=$$(basename $$d); for s in small medium large; do \
	  ./bin/deskwidgets preview $$t --size $$s -o build/previews/$$t-$$s.png >/dev/null & done; done; wait
	@ls build/previews | wc -l

screenshots: previews
	$(PYTHON) packaging/screenshots.py

clean:
	rm -rf build dist
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
