"""Ports of the crates htmlq depends on.

Each module here stands in for one Cargo dependency whose behaviour htmlq
exposes to its users, so replacing it with the nearest Python library would
change the program's output:

    html5ever.py   the HTML serialiser and its callback protocol
    kuchikiki.py   the DOM, its lazy iterators and its Rc/Weak link structure
    selectors.py   the CSS selector engine, as kuchikiki configures it
    url.py         the WHATWG URL parser (rust-url)

Parsing itself is not ported: html5lib already implements the same WHATWG
tree-construction algorithm html5ever does, and kuchikiki.py adapts its output.
See the dependency table in README.md for the reasoning on each.
"""
