#!/usr/bin/python

import signal, subprocess, time, urllib2

SAGE_ROOT = "/levi/scratch/robertwb/buildbot/sage-4.6/"
DATABASE = "../data"

# The server hangs while connecting to trac, so we poll it and
# restart if needed.

HTTP_TIMEOUT = 60
POLL_INTERVAL = 180
KILL_WAIT = 5

p = None
try:
    # Start mongodb
    mongo_process = subprocess.Popen(["mongod", "--port=21001", "--dbpath=" + DATABASE], stderr=subprocess.STDOUT)

    # Run the server
    while True:

        if p is None or p.poll() is not None:
            # The subprocess died.
            restart = True
        else:
            try:
                print "Testing url..."
                urllib2.urlopen("http://sage.math.washington.edu:21100/", timeout=HTTP_TIMEOUT)
                print "    ...good"
                restart = False
            except urllib2.URLError, e:
                print "    ...bad", e
                restart = True

        if restart:
            if p is not None and p.poll() is None:
                print "SIGTERM"
                p.send_signal(signal.SIGTERM)
                time.sleep(KILL_WAIT)
                if p.poll() is None:
                    print "SIGKILL"
                    p.kill()
                    time.sleep(KILL_WAIT)

            print "Starting server..."
            p = subprocess.Popen([SAGE_ROOT + "/local/bin/python", "serve.py", "--base=4.7", "--port=21100"])
            print "    ...done."
        time.sleep(POLL_INTERVAL)

finally:
    mongo_process.send_signal(signal.SIGTERM)
    if p is not None and p.poll() is None:
        p.kill()

