**PATCHBOT**

The patchbot only needs a SageMath source install (clone of git repository)
and is started with

    python -m sagemath_patchbot.patchbot --sage-root=XXX [other options]

Type --help for a list of options, though most configuration is done via an
optional JSON config file.

For more documentation on running a patchbot, see https://wiki.sagemath.org/patchbot

**SERVER**

The server needs a Python with Flask and mongodb installed.

