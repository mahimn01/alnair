"""clpresearch: a keyless, rate-limited, SSRF-safe research toolkit with an address-verified memory.

Plain English: tools that search scholarly and primary-source providers, save every fetched
source as exact text under ~/.clp/research, and let you quote it back with an address + hash
that the CLP verify gate can check. A passing verify proves the quoted words are in the saved
source text. It does NOT prove the source is true or that a claim follows from it.
"""

__version__ = "0.1.0"
