PYTHON := python3
ENVIRONMENT := env
LIBRARY := lib
SOURCE := magic_lights
BUILD := debug
OUTPUT := output/$(BUILD)
PY_MAIN := main.py
MPY_FILES := $(addprefix $(OUTPUT)/$(SOURCE)/,__init__.mpy magic_lights.mpy pacman.mpy firework.mpy ansi_color.mpy atomic_file.mpy engine_utils.mpy version.mpy)
LIBRARY_MICRODOT_MPY_FILES := $(addprefix $(OUTPUT)/microdot/,__init__.mpy microdot.mpy websocket.mpy helpers.mpy)
HTML_FILE = $(OUTPUT)/web/index.html.gz

ifndef SERIAL
SERIAL := $$(ls /dev/tty.usbmodem* | head -1)
endif

ifeq ($(BUILD),debug)
	PY_MAIN := main_debug.py
endif

define INCLUDE_LIBRARY
LIBRARY_MPY_FILES += $(1)
LIBRARY_DIRECTORIES += $(dir $(firstword $(1)))
$(1): $(dir $(firstword $(1)))%.mpy: $(LIBRARY)/$(2)/%.py
	$$(MPY_CROSS)
endef

define MPY_CROSS
	$(PYTHON) -m mpy_cross $< -o $@
endef

define RSHELL
	rshell -p $(SERIAL) --buffer-size 512 $(1)
endef

.PHONY: virtual virtual-activate virtual-deactivate patch requirements-install requirements-freeze lint build deploy clean repl

virtual: $(ENVIRONMENT)

# Usage: $(make virtual-activate)
virtual-activate: virtual
	@echo source $(ENVIRONMENT)/bin/activate

virtual-deactivate:
	deactivate

patch:
	patch -N $(ENVIRONMENT)/lib/$(PYTHON)*/site-packages/rshell/main.py < patches/rshell-time.patch

requirements-install:
	$(PYTHON) -m pip install --upgrade pip
	$(PYTHON) -m pip install -r requirements.txt

requirements-freeze:
	$(PYTHON) -m pip freeze > requirements.txt

lint:
	$(PYTHON) -m flake8 magic_lights main.py main_debug.py manifest.py

$(HTML_FILE): web/index.html
	cp web/index.html $(OUTPUT)/web
	gzip -f --best $(OUTPUT)/web/index.html

$(MPY_FILES): $(OUTPUT)/$(SOURCE)/%.mpy: $(SOURCE)/%.py
	$(MPY_CROSS)

$(OUTPUT)/main.py: $(PY_MAIN)
	cp $(PY_MAIN) $(OUTPUT)/main.py

$(eval $(call INCLUDE_LIBRARY,$(LIBRARY_MICRODOT_MPY_FILES),microdot/src/microdot))

$(MPY_FILES) $(OUTPUT)/main.py $(LIBRARY_MPY_FILES) $(HTML_FILE): | $(OUTPUT)

$(OUTPUT):
	mkdir -p $(OUTPUT)/$(SOURCE)
	mkdir -p $(LIBRARY_DIRECTORIES)
	mkdir -p $(OUTPUT)/web

build: $(MPY_FILES) $(OUTPUT)/main.py $(LIBRARY_MPY_FILES) $(HTML_FILE)

deploy: build
	$(call RSHELL,rsync $(OUTPUT) /pyboard)
	$(call RSHELL,repl '~ import machine ~ machine.reset() ~')
	@echo

clean:
	rm -rf output
	$(call RSHELL,rm -rf /pyboard/*)

repl:
	$(call RSHELL,repl)

$(ENVIRONMENT):
	$(PYTHON) -m venv $(ENVIRONMENT)
