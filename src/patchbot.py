#!/usr/bin/env python

####################################################################
#
# This is the main script for the patchbot. It pulls patches from
# trac, applies them, and publishes the results of the tests to a
# server running serve.py.  Configuration is primarily done via an
# optional conf.txt file passed in as a command line argument.
#
#          Author: Robert Bradshaw <robertwb@gmail.com>
#
#               Copyright 2010-11 (C) Google, Inc.
#
#  Distributed under the terms of the GNU General Public License (GPL)
#  as published by the Free Software Foundation; either version 2 of
#  the License, or (at your option) any later version.
#                  http://www.gnu.org/licenses/
####################################################################


import signal
import getpass, platform
import random, re, os, shutil, sys, subprocess, time, traceback
import cPickle as pickle
import bz2, urllib2, urllib, json
from optparse import OptionParser

from http_post_file import post_multipart

from trac import scrape, pull_from_trac
from util import now_str as datetime, parse_datetime, prune_pending, do_or_die, get_base, compare_version, current_reports
from plugins import PluginResult

def filter_on_authors(tickets, authors):
    if authors is not None:
        authors = set(authors)
    for ticket in tickets:
        if authors is None or set(ticket['authors']).issubset(authors):
            yield ticket

def contains_any(key, values):
    clauses = [{'key': value} for value in values]
    return {'$or': clauses}

def no_unicode(s):
    return s.encode('ascii', 'replace').replace(u'\ufffd', '?')

def compare_machines(a, b, machine_match=None):
    if isinstance(a, dict) or isinstance(b, dict):
        # old format, remove
        return (1,)
    else:
        if machine_match is not None:
            a = a[:machine_match]
            b = b[:machine_match]
        diff = [x != y for x, y in zip(a, b)]
        if len(a) != len(b):
            diff.append(1)
        return diff
    
class TimeOut(Exception):
    pass

def alarm_handler(signum, frame):
    raise Alarm

class Tee:
    def __init__(self, filepath, time=False, timeout=60*60*24):
        self.filepath = filepath
        self.time = time
        self.timeout = timeout
        
    def __enter__(self):
        self._saved = os.dup(sys.stdout.fileno()), os.dup(sys.stderr.fileno())
        self.tee = subprocess.Popen(["tee", self.filepath], stdin=subprocess.PIPE)
        os.dup2(self.tee.stdin.fileno(), sys.stdout.fileno())
        os.dup2(self.tee.stdin.fileno(), sys.stderr.fileno())
        if self.time:
            print datetime()
            self.start_time = time.time()
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is not None:
            traceback.print_exc()
        if self.time:
            print datetime()
            print int(time.time() - self.start_time), "seconds"
        self.tee.stdin.close()
        time.sleep(1)
        os.dup2(self._saved[0], sys.stdout.fileno())
        os.dup2(self._saved[1], sys.stderr.fileno())
        os.close(self._saved[0])
        os.close(self._saved[1])
        time.sleep(1)
        try:
            signal.signal(signal.SIGALRM, alarm_handler)
            signal.alarm(self.timeout)
            self.tee.wait()
            signal.alarm(0)
        except TimeOut:
            traceback.print_exc()
            raise
        return False


class Timer:
    def __init__(self):
        self._starts = {}
        self._history = []
        self.start()
    def start(self, label=None):
        self._last_activity = self._starts[label] = time.time()
    def finish(self, label=None):
        try:
            elapsed = time.time() - self._starts[label]
        except KeyError:
            elapsed = time.time() - self._last_activity
        self._last_activity = time.time()
        self.print_time(label, elapsed)
        self._history.append((label, elapsed))
    def print_time(self, label, elapsed):
        print label, '--', int(elapsed), 'seconds'
    def print_all(self):
        for label, elapsed in self._history:
            self.print_time(label, elapsed)

# The sage test scripts could really use some cleanup...
all_test_dirs = ["doc/common", "doc/en", "doc/fr", "sage"]

status = {
    'started'       : 'ApplyFailed',
    'applied'       : 'BuildFailed',
    'built'         : 'TestsFailed',
    'tested'        : 'TestsPassed',
    'tests_passed_plugins_failed': 'PluginFailed',
    'plugins'       : 'PluginOnly',
    'plugins_failed' : 'PluginOnlyFailed',
}

def plugin_boundary(name, end=False):
    if end:
        name = 'end ' + name
    return ' '.join(('='*10, name, '='*10))


def machine_data():
    system, node, release, version, arch = os.uname()
    if system.lower() == "linux":
        dist_name, dist_version, dist_id = platform.linux_distribution()
        if dist_name:
            return [dist_name, dist_version, arch, release, node]
    return [system, arch, release, node]

def parse_time_of_day(s):
    def parse_interval(ss):
        ss = ss.strip()
        if '-' in ss:
            start, end = ss.split('-')
            return float(start), float(end)
        else:
            return float(ss), float(ss) + 1
    return [parse_interval(ss) for ss in s.split(',')]

def check_time_of_day(hours):
    from datetime import datetime
    now = datetime.now()
    hour = now.hour + now.minute / 60.
    for start, end in parse_time_of_day(hours):
        if start < end:
            if start <= hour <= end:
                return True
        elif hour <= end or start <= hour:
            return True
    return False

class Patchbot:
    
    def __init__(self, sage_root, server, config_path, dry_run=False, plugin_only=False):
        self.sage_root = sage_root
        self.server = server
        self.base = get_base(sage_root)
        self.dry_run = dry_run
        self.plugin_only = plugin_only
        self.config_path = config_path
        self.reload_config()
        
    def load_json_from_server(self, path):
        handle = urllib2.urlopen("%s/%s" % (self.server, path))
        try:
            return json.load(handle)
        finally:
            handle.close()

    def default_trusted_authors(self):
        try:
            return self._default_trusted
        except:
            self._default_trusted = self.load_json_from_server("trusted").keys()
            return self._default_trusted

    def lookup_ticket(self, id):
        path = "ticket/?" + urllib.urlencode({'raw': True, 'query': json.dumps({'id': id})})
        res = self.load_json_from_server(path)
        if res:
            return res[0]
        else:
            return scrape(id)

    def get_config(self):
        if self.config_path is None:
            unicode_conf = {}
        else:
            unicode_conf = json.load(open(self.config_path))
        # defaults
        conf = {
            "idle": 300,
            "time_of_day": "0-0", # midnight-midnight
            "parallelism": 3,
            "timeout": 3 * 60 * 60,
            "plugins": ["plugins.commit_messages",
                        "plugins.coverage",
                        "plugins.trailing_whitespace",
                        "plugins.startup_modules",
    #                    "plugins.docbuild"
                        ],
            "bonus": {},
            "machine": machine_data(),
            "machine_match": 3,
            "user": getpass.getuser(),
        }
        default_bonus = {
            "needs_review": 1000,
            "positive_review": 500,
            "blocker": 100,
            "critical": 50,
        }
        for key, value in unicode_conf.items():
            conf[str(key)] = value
        for key, value in default_bonus.items():
            if key not in conf['bonus']:
                conf['bonus'][key] = value
        if "trusted_authors" not in conf:
            conf["trusted_authors"] = self.default_trusted_authors()
        
        def locate_plugin(name):
            ix = name.rindex('.')
            module = name[:ix]
            name = name[ix+1:]
            plugin = getattr(__import__(module, fromlist=[name]), name)
            assert callable(plugin)
            return plugin
        conf["plugins"] = [(name, locate_plugin(name)) for name in conf["plugins"]]
        return conf
    
    def reload_config(self):
        self.config = self.get_config()
        return self.config

    def get_ticket(self, return_all=False):
        trusted_authors = self.config.get('trusted_authors')
        query = "raw&status=open&todo"
        if trusted_authors:
            query += "&authors=" + urllib.quote_plus(no_unicode(':'.join(trusted_authors)), safe=':')
        all = self.load_json_from_server("ticket/?" + query)
        if trusted_authors:
            all = filter_on_authors(all, trusted_authors)
        all = filter(lambda x: x[0], ((self.rate_ticket(t), t) for t in all))
        all.sort()
        if return_all:
            return reversed(all)
        if all:
            return all[-1]

    def get_ticket_list(self):
        return self.get_ticket(return_all=True)

    def rate_ticket(self, ticket):
        rating = 0
        if ticket['spkgs']:
            return # can't handle these yet
        elif not ticket['patches']:
            return # nothing to do
        for dep in ticket['depends_on']:
            if isinstance(dep, basestring) and '.' in dep:
                if compare_version(self.base, dep) < 0:
                    # Depends on a newer version of Sage than we're running.
                    return None
        bonus = self.config['bonus']
        for author in ticket['authors']:
            if author not in self.config['trusted_authors']:
                return
            rating += bonus.get(author, 0)
        for participant in ticket['participants']:
            rating += bonus.get(participant, 0) # doubled for authors
        rating += len(ticket['participants'])
        # TODO: remove condition
        if 'component' in ticket:
            rating += bonus.get(ticket['component'], 0)
        rating += bonus.get(ticket['status'], 0)
        rating += bonus.get(ticket['priority'], 0)
        rating += bonus.get(str(ticket['id']), 0)
        redundancy = (100,)
        prune_pending(ticket)
        if not ticket.get('retry'):
            for reports in self.current_reports(ticket):
                redundancy = min(redundancy, compare_machines(reports['machine'], self.config['machine'], self.config['machine_match']))
        if not redundancy[-1]:
            return # already did this one
        return redundancy, rating, -int(ticket['id'])

    def current_reports(self, ticket):
        if isinstance(ticket, (int, str)):
            ticket = self.lookup_ticket(ticket)
        return current_reports(ticket, base=self.base)
    
    def test_a_ticket(self, ticket=None):
    
        self.reload_config()

        if ticket is None:
            ticket = self.get_ticket()
        else:
            ticket = None, scrape(int(ticket))
        if not ticket:
            print "No more tickets."
            if random.random() < 0.01:
                self.cleanup()
            time.sleep(conf['idle'])
            return

        rating, ticket = ticket
        print "\n" * 2
        print "=" * 30, ticket['id'], "=" * 30
        print ticket['title']
        print "score", rating
        print "\n" * 2
        log_dir = self.sage_root + "/logs"
        if not os.path.exists(log_dir):
            os.mkdir(log_dir)
        log = '%s/%s-log.txt' % (log_dir, ticket['id'])
        if not self.plugin_only:
            self.report_ticket(ticket, status='Pending', log=None)
        plugins_results = []
        try:
            with Tee(log, time=True, timeout=self.config['timeout']):
                t = Timer()
                start_time = time.time()

                state = 'started'
                os.environ['MAKE'] = "make -j%s" % self.config['parallelism']
                os.environ['SAGE_ROOT'] = self.sage_root
                # TODO: Ensure that sage-main is pristine.
                pull_from_trac(self.sage_root, ticket['id'], force=True)
                t.finish("Apply")
                state = 'applied'
                
                do_or_die('$SAGE_ROOT/sage -b %s' % ticket['id'])
                t.finish("Build")
                state = 'built'
                
                working_dir = "%s/devel/sage-%s" % (self.sage_root, ticket['id'])
                # Only the ones on this ticket.
                patches = os.popen2('hg --cwd %s qapplied' % working_dir)[1].read().strip().split('\n')[-len(ticket['patches']):]
                kwds = {
                    "original_dir": "%s/devel/sage-0" % self.sage_root,
                    "patched_dir": working_dir,
                    "patches": ["%s/devel/sage-%s/.hg/patches/%s" % (self.sage_root, ticket['id'], p) for p in patches if p],
                    "sage_binary": os.path.join(self.sage_root, 'sage')
                }
                
                for name, plugin in self.config['plugins']:
                    try:
                        if ticket['id'] != 0 and os.path.exists(os.path.join(log_dir, '0', name)):
                            baseline = pickle.load(open(os.path.join(log_dir, '0', name)))
                        else:
                            baseline = None
                        print plugin_boundary(name)
                        res = plugin(ticket, baseline=baseline, **kwds)
                        passed = True
                    except Exception:
                        traceback.print_exc()
                        passed = False
                        res = None
                    finally:
                        if isinstance(res, PluginResult):
                            if res.baseline is not None:
                                plugin_dir = os.path.join(log_dir, str(ticket['id']))
                                if not os.path.exists(plugin_dir):
                                    os.mkdir(plugin_dir)
                                pickle.dump(res.baseline, open(os.path.join(plugin_dir, name), 'w'))
                            passed = res.status == PluginResult.Passed
                            print name, res.status
                        plugins_results.append((name, passed))
                        t.finish(name)
                        print plugin_boundary(name, end=True)
                plugins_passed = all(passed for (name, passed) in plugins_results)
                
                if self.plugin_only:
                    state = 'plugins' if plugins_passed else 'plugins_failed'
                else:
                    if self.dry_run:
                        test_dirs = ["$SAGE_ROOT/devel/sage-%s/sage/misc/a*.py" % (ticket['id'])]
                    else:
                        test_dirs = ["-sagenb"] + ["$SAGE_ROOT/devel/sage-%s/%s" % (ticket['id'], dir) for dir in all_test_dirs]
                    if conf['parallelism'] > 1:
                        test_cmd = "-tp %s" % conf['parallelism']
                    else:
                        test_cmd = "-t"
                    do_or_die("$SAGE_ROOT/sage %s %s" % (test_cmd, ' '.join(test_dirs)))
                    t.finish("Tests")
                    state = 'tested'
                    
                    if not plugins_passed:
                        state = 'tests_passed_plugins_failed'

                print
                t.print_all()
        except urllib2.HTTPError:
            # Don't report failure because the network/trac died...
            traceback.print_exc()
            return 'Pending'
        except Exception:
            traceback.print_exc()
        
        for _ in range(5):
            try:
                print "Reporting", ticket['id'], status[state]
                if not self.dry_run:
                    self.report_ticket(ticket, status=status[state], log=log, plugins=plugins_results)
                print "Done reporting", ticket['id']
                break
            except urllib2.HTTPError:
                traceback.print_exc()
                time.sleep(conf['idle'])
        else:
            print "Error reporting", ticket['id']
        return status[state]

    def report_ticket(self, ticket, status, log, plugins=[]):
        print ticket['id'], status
        report = {
            'status': status,
            'patches': ticket['patches'],
            'deps': ticket['depends_on'],
            'spkgs': ticket['spkgs'],
            'base': self.base,
            'user': self.config['user'],
            'machine': self.config['machine'],
            'time': datetime(),
            'plugins': plugins,
        }
        fields = {'report': json.dumps(report)}
        if status != 'Pending':
            files = [('log', 'log', bz2.compress(open(log).read()))]
        else:
            files = []
        print post_multipart("%s/report/%s" % (self.server, ticket['id']), fields, files)

    def cleanup(self):
        print "Looking up closed tickets."
        # TODO: Get just the list, not the entire rendered page.
        closed_list = urllib2.urlopen(self.server + "?status=closed").read()
        closed = set(m.groups()[0] for m in re.finditer(r"/ticket/(\d+)/", closed_list))
        for branch in os.listdir(os.path.join(self.sage_root, "devel")):
            if branch[:5] == "sage-":
                if branch[5:] in closed:
                    to_delete = os.path.join(self.sage_root, "devel", branch)
                    print "Deleting closed ticket:", to_delete
                    shutil.rmtree(to_delete)
        print "Done cleaning up."

def main(args):
    global conf

    # Most configuration is done in the config file, which is reread between
    # each ticket for live configuration of the patchbot.
    parser = OptionParser()
    parser.add_option("--config", dest="config")
    parser.add_option("--sage-root", dest="sage_root", default=os.environ.get('SAGE_ROOT'))
    parser.add_option("--server", dest="server", default="http://patchbot.sagemath.org/")
    parser.add_option("--count", dest="count", default=1000000)
    parser.add_option("--ticket", dest="ticket", default=None)
    parser.add_option("--list", dest="list", default=False)
    parser.add_option("--full", action="store_true", dest="full", default=False)
    parser.add_option("--skip-base", action="store_true", dest="skip_base", default=False)
    parser.add_option("--dry-run", action="store_true", dest="dry_run", default=False)
    parser.add_option("--plugin-only", action="store_true", dest="plugin_only", default=False)
    (options, args) = parser.parse_args(args)
    
    conf_path = options.config and os.path.abspath(options.config)
    if options.ticket:
        tickets = [int(t) for t in options.ticket.split(',')]
        count = len(tickets)
    else:
        tickets = None
        count = int(options.count)

    patchbot = Patchbot(options.sage_root, options.server, conf_path, dry_run=options.dry_run, plugin_only=options.plugin_only)
    
    conf = patchbot.get_config()
    if options.list:
        count = sys.maxint if options.list is "True" else int(options.list)
        print "Getting ticket list..."
        for ix, (score, ticket) in enumerate(patchbot.get_ticket_list()):
            if ix >= count:
                break
            print score, '\t', ticket['id'], '\t', ticket['title']
            if options.full:
                print ticket
                print
        sys.exit(0)

    print "WARNING: Assuming sage-main is pristine."
    if options.sage_root == os.environ.get('SAGE_ROOT'):
        print "WARNING: Do not use this copy of sage while the patchbot is running."

    if not options.skip_base:
        def good(report):
            return report['machine'] == conf['machine'] and report['status'] == 'TestsPassed'
        if options.plugin_only or not any(good(report) for report in patchbot.current_reports(0)):
            res = patchbot.test_a_ticket(0)
            if res not in  ('TestsPassed', 'PluginOnly'):
                print "\n\n"
                while True:
                    print "Failing tests in your install: %s. Continue anyways? [y/N] " % res
                    ans = sys.stdin.readline().lower().strip()
                    if ans == '' or ans[0] == 'n':
                        sys.exit(1)
                    elif ans[0] == 'y':
                        break

    for k in range(count):
        try:
            if tickets:
                ticket = tickets.pop(0)
            else:
                ticket = None
            conf = patchbot.reload_config()
            if check_time_of_day(conf['time_of_day']):
                patchbot.test_a_ticket(ticket)
            else:
                print "Idle."
                time.sleep(conf['idle'])
        except urllib2.HTTPError:
                traceback.print_exc()
                time.sleep(conf['idle'])

if __name__ == '__main__':
    # allow this script to serve as a single entry point for bots and the server
    args = list(sys.argv)
    if len(args) > 1 and args[1] == '--serve':
        del args[1]
        from serve import main
    main(args)
