# Convenience targets for the htmlq Python migration.
#
# ORACLE_IMAGE is the Rust build of mgdm/htmlq used as the behavioural oracle;
# `make oracle` and `make sweep` need it, `make test` does not.
ORACLE_IMAGE ?= htmlq-rust-test
RUST_SRC     ?= ../../scraped_repos/Rust/mgdm_htmlq
DOCKER_DIR   ?= ../../docker

.PHONY: install test lint image docker-test oracle-image oracle sweep clean

install:
	pip install -e ".[test]"

test:
	python -m pytest -q

lint:
	python -m compileall -q htmlq tests tools

image:
	docker build -t htmlq-python-test .

docker-test: image
	docker run --rm --network none htmlq-python-test

oracle-image:
	docker build -f $(DOCKER_DIR)/htmlq-rust-test.Dockerfile -t $(ORACLE_IMAGE) $(RUST_SRC)

# Re-record tests/oracle.json from the original Rust binary.
oracle: oracle-image
	python tools/capture_oracle.py

# Cross-multiply documents x selectors x flags against both implementations.
sweep: oracle-image
	python tools/diff_sweep.py

clean:
	rm -rf build dist *.egg-info .pytest_cache
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
