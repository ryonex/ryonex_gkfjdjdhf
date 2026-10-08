"""unprom - a best-effort deobfuscator for the Prometheus Lua/Luau obfuscator
and its rebrands (e.g. wearedevs.net's obfuscator v1.0.0).

Quick start::

    from unprom import deobfuscate
    clean_source = deobfuscate(obfuscated_source)

Finer control::

    from unprom import Pipeline
    text, ctx = Pipeline(passes=["unwrap", "encrypt_strings", "constant_fold"],
                         options={"rename": "all"}).run(src)
    print(ctx.notes, ctx.stats)
"""

from .pipeline import deobfuscate, Pipeline, DEFAULT_PASSES, PassContext  # noqa: F401

__version__ = "0.1.0"
__all__ = ["deobfuscate", "Pipeline", "DEFAULT_PASSES", "PassContext", "__version__"]
