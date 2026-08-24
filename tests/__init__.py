"""Test suite for the htmlq Python migration.

`test_cli.py` and `test_link.py` are the migrated Rust tests; `test_parity.py`
replays `cases.py` against `oracle.json`, a recording of the original Rust
binary; `test_output_file.py` covers the `--output` path the parity harness
cannot observe.
"""
