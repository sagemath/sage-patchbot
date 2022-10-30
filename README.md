# PATCHBOT

The patchbot only needs a Sage source install (clone of git repository)
and is started with

    `python3 -m sage_patchbot.patchbot --sage-root=XXX [other options]`

Type `--help` for a list of options, though most configuration is done via an optional JSON config file.

For more documentation on running a patchbot, see [this page][1].

[1]: https://wiki.sagemath.org/patchbot

# SERVER

The server needs a Python with Flask and mongodb installed.
