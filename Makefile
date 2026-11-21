PYTHON ?= python3

.PHONY: test
test:
	PYTHONPATH=src $(PYTHON) -B -m unittest discover -s tests -t .
