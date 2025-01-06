PYTHON := python3
ENVIRONMENT := env
LIB := lib
SOURCE := magic_lights
BUILD := debug
OUTPUT := output/$(BUILD)
PY_MAIN := main.py
MPY_FILES := $(addprefix $(OUTPUT)/$(SOURCE)/,__init__.mpy magic_lights.mpy version.mpy)

ifndef SERIAL
SERIAL := $$(ls /dev/tty.usbmodem* | head -1)
endif

ifeq ($(BUILD),debug)
	PY_MAIN := main_debug.py
endif

define INCLUDE_LIBRARY
LIBRARY_MPY_FILES += $(1)
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

$(MPY_FILES): $(OUTPUT)/$(SOURCE)/%.mpy: $(SOURCE)/%.py
	$(MPY_CROSS)

$(OUTPUT)/main.py: $(PY_MAIN)
	cp $(PY_MAIN) $(OUTPUT)/main.py

$(MPY_FILES) $(OUTPUT)/main.py $(LIBRARY_MPY_FILES): | $(OUTPUT)

$(OUTPUT):
	mkdir -p $(OUTPUT)/$(SOURCE)

build: $(MPY_FILES) $(OUTPUT)/main.py $(LIBRARY_MPY_FILES)

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
