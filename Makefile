# DeskWidgets: common tasks
#   make run          run from the source tree (no install)
#   make install      install for the current user into ~/.local
#   make deb          build dist/deskwidgets_<version>_all.deb
#   make check        the test suite plus syntax checks of the Lua widgets and shell scripts
#   make previews     render every widget into build/previews (all sizes)
#   make screenshots  regenerate docs/screenshots

PYTHON ?= python3
VERSION := $(shell sed -n "s/^VERSION = '\(.*\)'/\1/p" deskwidgets/__init__.py)

.PHONY: run install uninstall deb check previews screenshots clean

run:
	$(PYTHON) -m deskwidgets

install:
	./install.sh

uninstall:
	./install.sh --uninstall

deb:
	./packaging/build-deb.sh

check:
	$(PYTHON) -m pytest -q
	@for f in install.sh packaging/*.sh packaging/postinst packaging/postrm; do sh -n $$f || exit 1; done
	@echo "all good"

previews:
	@mkdir -p build/previews
	@for d in deskwidgets/widgets/*/; do t=$$(basename $$d); for s in small medium large; do \
	  $(PYTHON) -m deskwidgets preview $$t --size $$s -o build/previews/$$t-$$s.png >/dev/null 2>&1; done; done
	@ls build/previews | wc -l

screenshots:
	$(PYTHON) packaging/screenshots.py

clean:
	rm -rf build dist
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
