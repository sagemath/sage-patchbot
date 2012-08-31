#!/usr/bin/env python

import os, signal, subprocess, sys, time, traceback, urllib2

if not hasattr(subprocess.Popen, 'send_signal'):
    def send_signal(self, sig):
        os.kill(self.pid, sig)
    subprocess.Popen.send_signal = send_signal

DATABASE = "../data"

# The server hangs while connecting to trac, so we poll it and
# restart if needed.

HTTP_TIMEOUT = 60
POLL_INTERVAL = 180
KILL_WAIT = 5

open("keepalive", "w").write(os.getpid())

p = None
try:
    # Start mongodb
    mongo_process = subprocess.Popen(["mongod", "--port=21002", "--dbpath=" + DATABASE], stderr=subprocess.STDOUT)

    # Run the server
    while True:
    
        if not os.path.exists("keepalive"):
            break

        if p is None or p.poll() is not None:
            # The subprocess died.
            restart = True
        else:
            try:
                print "Testing url..."
                urllib2.urlopen("http://patchbot.sagemath.org/", timeout=HTTP_TIMEOUT)
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
            base = open("base.txt").read().strip()
            p = subprocess.Popen([sys.executable, "serve.py", "--base=" + base, "--port=21100"])
            open("server.pid", "w").write(str(p.pid))
            print "    ...done."
        time.sleep(POLL_INTERVAL)

finally:
    traceback.print_exc()
    mongo_process.send_signal(signal.SIGTERM)
    if p is not None and p.poll() is None:
        p.kill()

