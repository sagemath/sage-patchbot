# PATCHBOT

The patchbot only needs a Sage source install (clone of git repository)
and is started with

    `python patchbot.py [options]`

Type `--help` for a list of options, though most configuration is done via an optional JSON config file. This is what is invoked by

    `sage --patchbot [...]`

For more documentation on running a patchbot, see [this page][1].

[1]: http://wiki.sagemath.org/buildbot/details

# SERVER

The server needs a Python with Flask and mongodb installed. Installing numpy and PIL (pillow) will allow multi-colored blurbs.

Start a monitoring loop with

    `python run_server.py` (THIS IS OBSOLETE, now using serve.wsgi)

Currently, the server is set up to run on port 21100, communicating with a mongod instance running on 21002.
