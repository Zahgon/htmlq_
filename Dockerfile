# Test image for the Python migration of mgdm/htmlq.
# Build context must be the package root: migrated_repo_python/mgdm_htmlq
#
#   docker build -t htmlq-python-test migrated_repo_python/mgdm_htmlq
#   docker run --rm --network none htmlq-python-test
#
# The parity suite replays tests/oracle.json -- a recording of the real Rust
# binary's stdout/stderr/exit status -- so it needs no network and no Rust
# toolchain at run time. Regenerating that recording does need the Rust image;
# see tools/capture_oracle.py.
FROM python:3.12-slim

WORKDIR /src

# Dependencies first, so editing a source file does not re-resolve them.
COPY pyproject.toml README.md LICENSE.md ./
COPY htmlq ./htmlq
COPY tests ./tests
COPY tools ./tools

RUN pip install --no-cache-dir --disable-pip-version-check ".[test]"

# The suite shells out to `python -m htmlq`, so the package has to be importable
# from the working directory as well as from site-packages.
ENV PYTHONPATH=/src

CMD ["python", "-m", "pytest", "-q"]
