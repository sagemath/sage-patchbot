The server needs a Python with Flask and mongod installed.  Start a monitoring loop with

    python run_server.py

The patchbot only needs a Sage install and is started with

    python buildbot.py --server=http://patchbot.sagemath.org --sage=$SAGE_ROOT --config=../conf.txt

Eventually both of these will get merged into Sage, the patchbot can be run 
anywhere (e.g. sage -patchbot --server=...), and there would be one central
server.

Currently, the server is set up to run on port 21100, communicating with
a mongod instance running on 21001.
