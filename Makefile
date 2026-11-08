# DeskWidgets: common tasks
#   make run          run from the source tree (no install), widgets are read from the checkout
#   make build        optimised binary in target/release
#   make install      install for the current user into ~/.local
#   make deb          build dist/deskwidgets_<version>_<arch>.deb
#   make check        the test suite plus syntax checks of the Lua widgets and shell scripts
#   make previews     render every widget into build/previews (all sizes)
#   make screenshots  regenerate docs/screenshots

CARGO ?= cargo
BIN := target/release/deskwidgets

.PHONY: run build install uninstall deb check previews screenshots clean

run:
	DESKWIDGETS_DATA=$(CURDIR) $(CARGO) run --release --

build:
	$(CARGO) build --release

install:
	./install.sh

uninstall:
	./install.sh --uninstall

deb:
	./packaging/build-deb.sh

check:
	$(CARGO) test
	@for f in install.sh packaging/*.sh packaging/postinst packaging/postrm; do sh -n $$f || exit 1; done
	@echo "all good"

previews: build
	@mkdir -p build/previews
	@for d in widgets/*/; do t=$$(basename $$d); for s in small medium large; do \
	  DESKWIDGETS_DATA=$(CURDIR) $(BIN) preview $$t --size $$s -o build/previews/$$t-$$s.png >/dev/null 2>&1; done; done
	@ls build/previews | wc -l

screenshots:
	DESKWIDGETS_DATA=$(CURDIR) $(CARGO) run --release --example screenshots

clean:
	$(CARGO) clean
	rm -rf build dist
