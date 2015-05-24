#!/usr/bin/env python
# -*- coding: utf-8 -*-

####################################################################
#
# This is the main script for the patchbot. It pulls patches from
# trac, applies them, and publishes the results of the tests to a
# server running serve.py.  Configuration is primarily done via an
# optional conf.txt file passed in as a command line argument.
#
#          Author: Robert Bradshaw <robertwb@gmail.com>
#
#               Copyright 2010-14 (C) Google, Inc.
#
#  Distributed under the terms of the GNU General Public License (GPL)
#  as published by the Free Software Foundation; either version 2 of
#  the License, or (at your option) any later version.
#                  http://www.gnu.org/licenses/
####################################################################

import codecs
import hashlib
import signal
import getpass
import platform
import glob
import re
import os
import shutil
import sys
import subprocess
import time
import traceback
import tempfile
import cPickle as pickle
import bz2
import urllib2
import urllib
import json
import socket

from optparse import OptionParser
from http_post_file import post_multipart
from trac import scrape, pull_from_trac
from util import (now_str as datetime, prune_pending, do_or_die,
                  get_version, current_reports, git_commit,
                  describe_branch, compare_version, temp_build_suffix,
                  ensure_free_space,
                  ConfigException, SkipTicket)
import version as patchbot_version
from plugins import PluginResult

# name of the log files
LOG_RATING = 'rating.log'
LOG_RATING_SHORT = 'rating_summary.txt'
LOG_MAIN = ('patchbot.log', sys.stdout)
LOG_MAIN_SHORT = 'history.txt'
LOG_CONFIG = 'config.txt'


def filter_on_authors(tickets, authors):
    """
    Keep only tickets with authors among the given ones.

    Every ticket is a dict.

    INPUT:

    a list of tickets and a list of authors

    OUTPUT:

    a list of tickets
    """
    if authors is not None:
        authors = set(authors)
    for ticket in tickets:
        if authors is None or set(ticket['authors']).issubset(authors):
            yield ticket


def compare_machines(a, b, machine_match=None):
    """
    Compare two machines a and b.

    Return a list.

    machine_match is a number of initial things to look at.

    >>> m1 = ['Ubuntu', '14.04', 'i686', '3.13.0-40-generic', 'arando']
    >>> m2 = ['Fedora', '19', 'x86_64', '3.10.4-300.fc19.x86_64', 'desktop']
    >>> compare_machines(m1, m2)
    """
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
    """
    ? Alarm is not defined ? what is this ?
    """
    raise Alarm


class Tee:
    def __init__(self, filepath, time=False, timeout=None, timer=None):
        if timeout is None:
            timeout = 60 * 60 * 24
        self.filepath = filepath
        self.time = time
        self.timeout = timeout
        self.timer = timer

    def __enter__(self):
        self._saved = os.dup(sys.stdout.fileno()), os.dup(sys.stderr.fileno())
        self.tee = subprocess.Popen(["tee", self.filepath],
                                    stdin=subprocess.PIPE)
        os.dup2(self.tee.stdin.fileno(), sys.stdout.fileno())
        os.dup2(self.tee.stdin.fileno(), sys.stderr.fileno())
        if self.time:
            print(datetime())
            self.start_time = time.time()

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.timer:
            self.timer.print_all()
        if exc_type is not None:
            traceback.print_exc()
        if self.time:
            print(datetime())
            print(int(time.time() - self.start_time), "seconds")
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
        print(label, '--', int(elapsed), 'seconds')

    def print_all(self):
        for label, elapsed in self._history:
            self.print_time(label, elapsed)

status = {'started': 'ApplyFailed',
          'applied': 'BuildFailed',
          'built': 'TestsFailed',
          'tested': 'TestsPassed',
          'tests_passed_plugins_failed': 'PluginFailed',
          'plugins': 'PluginOnly',
          'plugins_failed': 'PluginOnlyFailed',
          'spkg': 'Spkg',
          'network_error': 'Pending',
          'skipped': 'Pending'}


def plugin_boundary(name, end=False):
    """
    Return the text that bounds the plugins in the reports.
    """
    if end:
        name = 'end ' + name
    return ' '.join(('=' * 10, name, '=' * 10))


def machine_data():
    """
    Return the machine data as a list of strings

    This uses uname to find the data.

    m1 = ['Ubuntu', '14.04', 'i686', '3.13.0-40-generic', 'arando']
    m2 = ['Fedora', '19', 'x86_64', '3.10.4-300.fc19.x86_64', 'desktop']
    """
    system, node, release, version, arch = os.uname()
    if system.lower() == "linux":
        dist_name, dist_version, dist_id = platform.linux_distribution()
        if dist_name:
            return [dist_name, dist_version, arch, release, node]
    return [system, version, arch, release, node]


def parse_time_of_day(s):
    """
    Parse the 'time_of_day' config.

    Examples of syntax: default is "0-0" from midnight to midnight

    "06-18" start and end hours

    "22-07" idem during the night

    "10-12,14-18" several time ranges

    "17" for just one hour starting at given time
    """
    def parse_interval(ss):
        ss = ss.strip()
        if '-' in ss:
            start, end = ss.split('-')
            return float(start), float(end)
        else:
            return float(ss), float(ss) + 1
    return [parse_interval(ss) for ss in s.split(',')]


def check_time_of_day(hours):
    """
    Check that the time is inside the allowed running hours
    """
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


def sha1file(path, blocksize=None):
    """
    Return SHA-1 of file.

    This is used to check spkgs.
    """
    if blocksize is None:
        blocksize = 2 ** 16
    h = hashlib.sha1()
    handle = open(path)
    buf = handle.read(blocksize)
    while len(buf) > 0:
        h.update(buf)
        buf = handle.read(blocksize)
    return h.hexdigest()


class Patchbot:
    """
    Main class of the patchbot.

    This can be used in an interactive python or ipython session:

    >>> from patchbot import Patchbot
    >>> P = Patchbot('/homes/leila/sage','http://patchbot.sagemath.org',None,False,True,None)
    >>> import os
    >>> os.chdir(P.sage_root)
    >>> P.test_a_ticket(12345)

    How to more or less ban an author: have

    {"bonus":{"proust":-1000}}

    written inside the config.json file passed using --config=config.json
    """
    def __init__(self, sage_root, server, config_path, dry_run,
                 plugin_only, options):

        self.sage_root = sage_root
        self.server = server
        self.base = get_version(sage_root)
        self.dry_run = dry_run
        self.plugin_only = plugin_only
        self.config_path = config_path

        self.log_dir = os.path.join(self.sage_root, 'logs', 'patchbot')
        if not os.path.exists(self.log_dir):
            os.makedirs(self.log_dir)
        # Make sure this file is writable.
        handle = open(os.path.join(self.log_dir, 'install.log'), 'a')
        handle.close()

        self._version = patchbot_version.get_version()
        self.last_pull = 0
        self.to_skip = {}

        self.write_log('Launching patchbot {} with SAGE_ROOT={}'.format(
            self._version,
            self.sage_root), LOG_MAIN)

        self.reload_config()

        if options is None:
            # ugly workaround to simplify interactive use of Patchbot

            class opt:
                safe_only = True
            self.options = opt
        else:
            self.options = options

    def write_log(self, msg, logfile=None, date=True):
        r"""
        Write ``msg`` in a logfile.

        INPUT:

        - ``logfile`` -- (optional)

           * if not provided, write on stdout

           * if it is a string then append ``msg`` to the file ``logfile``

           * if it is a tuple or a list, then call write_log for each member of
             that list

        - ``date`` -- (default ``True``) whether to write the date at the begining of
          the line
        """
        if logfile is None:
            logfile = sys.stdout
            close = False
        elif isinstance(logfile, str):
            filename = os.path.join(self.log_dir, logfile)
            logfile = codecs.open(filename, 'a', encoding='utf-8')
            close = True
        elif isinstance(logfile, (tuple, list)):
            for f in logfile:
                self.write_log(msg, f, date)
            return
        elif isinstance(logfile, file):
            close = False
        else:
            raise ValueError("logfile = {} must be either None, or a string or a list or a file".format(logfile))

        if date:
            logfile.write("[{}] ".format(datetime()))
        logfile.write(msg)
        logfile.write("\n")

        if close:
            logfile.close()
        else:
            logfile.flush()

    def delete_log(self, logfile):
        r"""
        Delete ``logfile``
        """
        filename = os.path.join(self.log_dir, logfile)
        if os.path.isfile(filename):
            os.remove(filename)

    def version(self):
        """
        Return the version of the patchbot.

        Something like '2.3.3'
        """
        return self._version

    def banner(self):
        """
        A banner for the patchbot
        """
        red = '\033[31m'
        col_out = '\033[0m'
        s = u'┌─┬──────┐\n'
        s += u'│░│ ' + red + u'⊙  ʘ' + col_out + u' │        SageMath patchbot\n'
        s += u'│░│      │\n'
        s += u'│░│ ──── │        version {}\n'.format(self.version())
        s += u'╘═╧══════╛'
        return s.encode('utf8')

    def load_json_from_server(self, path, retry=1):
        """
        Load a json file from the server.

        INPUT:

        - ``path`` -- the query for the server

        - ``retry`` -- the number of times we retry to get a connection
        """
        while True:
            retry -= 1
            try:
                handle = urllib2.urlopen("{}/{}".format(self.server, path), timeout=10)
                ans = json.load(handle)
                handle.close()
                return ans
            except urllib2.HTTPError as err:
                self.write_log(" retry {}; {}".format(retry, str(err)), [LOG_MAIN, LOG_MAIN_SHORT])
                if retry == 0:
                    raise
            except socket.timeout as err:
                self.write_log(" retry {}; timeout while querying the patchbot server with '{}'".format(retry, path), [LOG_MAIN, LOG_MAIN_SHORT])
                if retry == 0:
                    raise
            else:
                break

            time.sleep(30)

    def default_trusted_authors(self):
        """
        Define the default trusted authors.

        They are computed by ``trusted_authors`` in serve.py
        """
        try:
            return self._default_trusted
        except AttributeError:
            self.write_log("Getting trusted author list...", LOG_MAIN)
            trusted = self.load_json_from_server("trusted", retry=10).keys()
            trusted += [u'git', u'vbraun_spam']
            self._default_trusted = set(trusted)
            return self._default_trusted

    def lookup_ticket(self, id, verbose=False):
        """
        Retrieve information about one ticket from the patchbot server.

        (or from trac if patchbot does not answer)

        For an example of the page it calls:

        http://patchbot.sagemath.org/ticket/?raw&query={"id":11529}

        For humans:

        http://patchbot.sagemath.org/ticket/?raw&query={"id":11529}&pretty
        """
        path = "ticket/?" + urllib.urlencode({'raw': True,
                                              'query': json.dumps({'id': id})})
        res = self.load_json_from_server(path, retry=3)
        if res:
            if verbose:
                print('lookup using json')
            return res[0]
        else:
            # trying using trac server instead
            if verbose:
                print('lookup using scrape')
            return scrape(id)

    def get_config(self):
        """
        Return the configuration.

        Either by default, or from a json config file.
        """
        if self.config_path is None:
            unicode_conf = {}
        else:
            unicode_conf = json.load(open(self.config_path))
        # defaults
        conf = {"idle": 300,
                "time_of_day": "0-0",  # midnight-midnight
                "parallelism": 3,
                "timeout": 3 * 60 * 60,
                "plugins": ["plugins.commit_messages",
                            "plugins.coverage",
                            "plugins.non_ascii",
                            "plugins.doctest_continuation",
                            "plugins.raise_statements",
                            # "plugins.trailing_whitespace",
                            "plugins.startup_time",
                            "plugins.startup_modules",
                            # "plugins.docbuild_pdf", # not yet tested
                            # "plugins.docbuild",  # already done once in make
                            "plugins.git_rev_list"],
                "bonus": {},
                "machine": machine_data(),
                "machine_match": 5,
                "user": getpass.getuser(),
                "keep_open_branches": True,
                "base_repo": "git://github.com/sagemath/sage.git",
                "base_branch": "develop",
                "max_behind_commits": 1,
                "max_behind_days": 1.0,
                "use_ccache": True,
                "safe_only": True,
                "skip_base": False}
        default_bonus = {"needs_review": 1000,
                         "positive_review": 500,
                         "blocker": 100,
                         "critical": 60,
                         "major": 10,
                         "unique": 40,
                         "applies": 20,
                         "behind": 1}

        for key, value in unicode_conf.items():
            conf[str(key)] = value

        for key, value in default_bonus.items():
            if key not in conf['bonus']:
                conf['bonus'][key] = value

        if "trusted_authors" not in conf:
            conf["trusted_authors"] = self.default_trusted_authors()
        if "extra_trusted_authors" in conf:
            conf["trusted_authors"].update(conf["extra_trusted_authors"])

        def locate_plugin(name):
            ix = name.rindex('.')
            module = name[:ix]
            name = name[ix + 1:]
            plugin = getattr(__import__(module, fromlist=[name]), name)
            assert callable(plugin)
            return plugin
        conf["plugins"] = [(name, locate_plugin(name))
                           for name in conf["plugins"]]

        self.delete_log(LOG_CONFIG)
        import pprint
        self.write_log("Configuration for the patchbot\n{}\n".format(datetime()), LOG_CONFIG, False)
        self.write_log(pprint.pformat(conf), LOG_CONFIG, False)

        if self.to_skip:
            s = ', '.join('#{} (until {})'.format(k, v)
                          for k, v in self.to_skip.iteritems())
            self.write_log('The following tickets will be skipped: ' + s, LOG_MAIN)

        return conf

    def reload_config(self):
        """
        Reload the configuration.
        """
        self.config = self.get_config()
        return self.config

    def check_base(self):
        """
        Check that the patchbot/base is synchro with 'base_branch'.

        Usually 'base_branch' is set to 'develop'.
        """
        self.write_log("Check base.", LOG_MAIN)
        cwd = os.getcwd()
        os.chdir(self.sage_root)
        try:
            do_or_die("git checkout patchbot/base")
        except Exception:
            do_or_die("git checkout -b patchbot/base")

        do_or_die("git fetch %s +%s:patchbot/base_upstream" %
                  (self.config['base_repo'], self.config['base_branch']))

        only_in_base = int(subprocess.check_output(["git", "rev-list", "--count", "patchbot/base_upstream..patchbot/base"]))

        only_in_upstream = int(subprocess.check_output(["git", "rev-list", "--count", "patchbot/base..patchbot/base_upstream"]))

        max_behind_time = self.config['max_behind_days'] * 60 * 60 * 24
        if (only_in_base > 0
                or only_in_upstream > self.config['max_behind_commits']
                or (only_in_upstream > 0 and
                    time.time() - self.last_pull < max_behind_time)):
            do_or_die("git checkout patchbot/base_upstream")
            do_or_die("git branch -f patchbot/base patchbot/base_upstream")
            do_or_die("git checkout patchbot/base")
            self.last_pull = time.time()
            os.chdir(cwd)
            return False
        os.chdir(cwd)
        return True

    def human_readable_base(self):
        """
        Return the human name of the base.
        """
        # TODO: Is this stable?
        version = get_version(self.sage_root)
        commit_count = subprocess.check_output(['git', 'rev-list', '--count', '%s..patchbot/base' % version])
        return "%s + %s commits" % (version, commit_count.strip())

    def get_one_ticket(self, status='open', verbose=0):
        """
        Return one ticket with its rating.

        INPUT:

        - ``verbose`` - if set to 0 then nothing is print on stdout, if 1 then
          only the summary is print on stdout and if 2 then also the details of
          the rating

        OUTPUT:

        A pair (rating, ticket data). The rating is a tuple of integer values.
        """
        query = "raw&status={}".format(status)

        self.write_log("Getting ticket list...", LOG_MAIN)
        all = self.load_json_from_server("ticket/?" + query, retry=10)

        # remove all tickets with None rating
        self.delete_log(LOG_RATING)
        all = filter(lambda x: x[0] is not None,
                     ((self.rate_ticket(t, verbose=(verbose == 2)), t)
                      for t in all))

        # sort tickets using their ratings
        all.sort()

        self.delete_log(LOG_RATING_SHORT)
        if verbose >= 1:
            logfile = [LOG_RATING_SHORT, sys.stdout]
        else:
            logfile = [LOG_RATING_SHORT]
        for rating, ticket in reversed(all):
            self.write_log(u'#{:<6}{:30}{}'.format(ticket['id'], rating[:2], ticket['title']),
                           logfile, date=False)

        return all[-1]

    def rate_ticket(self, ticket, verbose=False):
        """
        Evaluate the interest to test this ticket.

        Return nothing when the ticket should not be tested.
        """
        with open(os.path.join(self.log_dir, LOG_RATING), "a") as log_rating:

            if verbose:
                logfile = [log_rating, sys.stdout]
            else:
                logfile = [log_rating]

            if isinstance(ticket, (int, str)):
                ticket = self.lookup_ticket(ticket)

            rating = 0
            if ticket['id'] == 0:
                return ((100), 100, 0)

            if not ticket.get('git_branch'):
                self.write_log('#{}: no git branch'.format(ticket['id']), logfile)
                return

            if not(ticket['status'] in ('needs_review', 'positive_review',
                                        'needs_info', 'needs_work')):
                self.write_log('#{}: bad status (={})'.format(ticket['id'], ticket['status']), logfile)
                return

            self.write_log(u"#{}: start rating".format(ticket['id']), logfile)

            if ticket['milestone'] in ('sage-duplicate/invalid/wontfix',
                                       'sage-feature', 'sage-pending',
                                       'sage-wishlist'):
                self.write_log('  do not test if the milestone is not good (got {})'.format(ticket['milestone']),
                               logfile, False)
                return

            bonus = self.config['bonus']  # load the dict of bonus

            for author in ticket['authors']:
                if author not in self.config['trusted_authors']:
                    self.write_log('  do not test if some author is not trusted (got {})'.format(author),
                                   logfile, False)
                    return
                rating += 2 * bonus.get(author, 0)  # bonus for authors

            self.write_log('  rating {} after authors'.format(rating),
                           logfile, False)

            for participant in ticket['participants']:
                if participant not in self.config['trusted_authors']:
                    self.write_log('  do not test if some participant is not trusted (got {})'.format(participant),
                                   logfile, False)
                    return
                rating += bonus.get(participant, 0)  # bonus for participants
            self.write_log('  rating {} after participants'.format(rating),
                           logfile, False)

            if 'component' in ticket:
                rating += bonus.get(ticket['component'], 0)  # bonus for components

            self.write_log('  rating {} after components'.format(rating),
                           logfile, False)

            rating += bonus.get(ticket['status'], 0)
            rating += bonus.get(ticket['priority'], 0)
            rating += bonus.get(str(ticket['id']), 0)

            msg = '  rating {} after status ({})/priority ({})/id ({})'
            self.write_log(msg.format(rating, ticket['status'],
                                      ticket['priority'], ticket['id']),
                           logfile, False)

            prune_pending(ticket)

            retry = ticket.get('retry', False)
            # by default, do not retry the ticket

            uniqueness = (100,)
            # now let us look at previous reports
            if not retry:
                self.write_log('  start report scanning', logfile, False)
                for report in self.current_reports(ticket, newer=True):
                    if report.get('git_base'):
                        try:
                            only_in_base = int(subprocess.check_output(["git", "rev-list", "--count", "%s..patchbot/base" % report['git_base']],
                                                                       stderr=subprocess.PIPE))
                        except (ValueError, subprocess.CalledProcessError):
                            # report['git_base'] not in our repo
                            self.write_log('  commit {} not in the local git repository'.format(report['git_base']),
                                           logfile, date=False)
                            only_in_base = -1
                        rating += bonus['behind'] * only_in_base
                    self.write_log('  rating {} after behind'.format(rating),
                                   logfile, False)

                    report_uniqueness = compare_machines(report['machine'],
                                                         self.config['machine'],
                                                         self.config['machine_match'])
                    report_uniqueness = tuple(int(x) for x in report_uniqueness)
                    if only_in_base and not any(report_uniqueness):
                        report_uniqueness = (0, 0, 0, 0, 1)
                    uniqueness = min(uniqueness, report_uniqueness)

                    if report['status'] != 'ApplyFailed':
                        rating += bonus.get("applies", 0)
                    self.write_log('  rating {} after applies'.format(rating),
                                   logfile, False)
                    rating -= bonus.get("unique", 0)
                    self.write_log('  rating {} after uniqueness'.format(rating),
                                   logfile, False)
            self.write_log('  rating {} after report scanning'.format(rating),
                           logfile, False)

            if not any(uniqueness):
                self.write_log('  already done', logfile, False)
                return

            if ticket['id'] in self.to_skip:
                if self.to_skip[ticket['id']] < time.time():
                    del self.to_skip[ticket['id']]
                else:
                    self.write_log('  do not test if still in the skip delay',
                                   logfile, False)
                    return

            return uniqueness, rating, -int(ticket['id'])

    def current_reports(self, ticket, newer=False):
        """
        Return the list of current reports on a ticket.
        """
        if isinstance(ticket, (int, str)):
            ticket = self.lookup_ticket(ticket)
        return current_reports(ticket, base=self.base, newer=newer)

    def test_a_ticket(self, ticket=None):
        """
        Launch the test of a ticket.

        INPUT:

        - ``ticket``

          * if ``None`` then pick a ticket using :meth:`get_one_ticket`

          * if an integer or a string, use this ticket number
        """
        self.reload_config()

        if ticket is None:
            rating, ticket = self.get_one_ticket()
            self.write_log('testing found ticket #{}'.format(ticket['id']), LOG_MAIN)
        else:
            N = int(ticket)
            ticket = self.lookup_ticket(N)
            rating = None
            self.write_log('testing given ticket #{}'.format(N), LOG_MAIN)
        if not ticket:
            self.write_log('no more tickets, take a nap',
                           [LOG_MAIN, LOG_MAIN_SHORT])
            time.sleep(self.config['idle'])
            return

        if ticket['id'] == 0:
            self.write_log('testing the base', LOG_MAIN)
            rating = 100

        if rating is None:
            self.write_log("warning: rating is None, testing #{} at your own risk".format(ticket['id']),
                           LOG_MAIN)

        if not(ticket.get('git_branch') or ticket['id'] == 0):
            self.write_log("no git branch for #{}, hence no testing".format(ticket['id']),
                           LOG_MAIN)
            return

        print("\n" * 2)
        print("=" * 30, ticket['id'], "=" * 30)
        # title = unicode(ticket['title'], errors='ignore')
        # print(title)
        print(ticket['title'])
        print("score", rating)
        print("\n" * 2)
        log = '%s/%s-log.txt' % (self.log_dir, ticket['id'])
        self.write_log('#{}: starting tests'.format(ticket['id']), [LOG_MAIN, LOG_MAIN_SHORT])
        if not self.plugin_only:
            self.report_ticket(ticket, status='Pending', log=log)
        plugins_results = []
        try:
            t = Timer()
            with Tee(log, time=True, timeout=self.config['timeout'], timer=t):
                print(self.banner())

                if ticket['spkgs']:
                    state = 'spkg'
                    print "\n".join(ticket['spkgs'])
                    print
                    for spkg in ticket['spkgs']:
                        print
                        print '+' * 10, spkg, '+' * 10
                        print
                        try:
                            self.check_spkg(spkg)
                        except Exception:
                            traceback.print_exc()
                        t.finish(spkg)
                    self.to_skip[ticket['id']] = time.time() + 12 * 60 * 60

                if not ticket['spkgs']:
                    state = 'started'
                    os.environ['MAKE'] = "make -j%s" % self.config['parallelism']
                    os.environ['SAGE_ROOT'] = self.sage_root
                    os.environ['GIT_AUTHOR_NAME'] = os.environ['GIT_COMMITTER_NAME'] = 'patchbot'
                    os.environ['GIT_AUTHOR_EMAIL'] = os.environ['GIT_COMMITTER_EMAIL'] = 'patchbot@localhost'
                    os.environ['GIT_AUTHOR_DATE'] = os.environ['GIT_COMMITTER_DATE'] = '1970-01-01T00:00:00'
                    pull_from_trac(self.sage_root, ticket['id'], force=True,
                                   use_ccache=self.config['use_ccache'],
                                   safe_only=self.options.safe_only)
                    t.finish("Apply")
                    state = 'applied'
                    if not self.plugin_only:
                        self.report_ticket(ticket, status='Pending',
                                           log=log, pending_status=state)

                    do_or_die("$MAKE doc-clean")
                    do_or_die("$MAKE")
                    t.finish("Build")
                    state = 'built'
                    if not self.plugin_only:
                        self.report_ticket(ticket, status='Pending',
                                           log=log, pending_status=state)

                    # TODO: Exclude dependencies.
                    patch_dir = tempfile.mkdtemp()
                    if ticket['id'] != 0:
                        do_or_die("git format-patch -o '%s' patchbot/base..patchbot/ticket_merged" % patch_dir)

                    kwds = {
                        "patches": [os.path.join(patch_dir, p)
                                    for p in os.listdir(patch_dir)],
                        "sage_binary": os.path.join(os.getcwd(), 'sage'),
                        "dry_run": self.dry_run,
                    }

                    for name, plugin in self.config['plugins']:
                        try:
                            if ticket['id'] != 0 and os.path.exists(os.path.join(self.log_dir, '0', name)):
                                baseline = pickle.load(open(os.path.join(self.log_dir, '0', name)))
                            else:
                                baseline = None
                            print plugin_boundary(name)
                            do_or_die("git checkout patchbot/ticket_merged")
                            res = plugin(ticket, is_git=True,
                                         baseline=baseline, **kwds)
                            passed = True
                        except Exception:
                            traceback.print_exc()
                            passed = False
                            res = None
                        finally:
                            if isinstance(res, PluginResult):
                                if res.baseline is not None:
                                    plugin_dir = os.path.join(self.log_dir,
                                                              str(ticket['id']))
                                    if not os.path.exists(plugin_dir):
                                        os.mkdir(plugin_dir)
                                    pickle.dump(res.baseline, open(os.path.join(plugin_dir, name), 'w'))
                                    passed = res.status == PluginResult.Passed
                                    print name, res.status
                                    plugins_results.append((name, passed,
                                                            res.data))
                            else:
                                plugins_results.append((name, passed, None))
                            t.finish(name)
                            print plugin_boundary(name, end=True)
                    plugins_passed = all(passed for (name, passed, data)
                                         in plugins_results)
                    self.report_ticket(ticket, status='Pending', log=log,
                                       pending_status='plugins_passed'
                                       if plugins_passed else 'plugins_failed')

                    if self.plugin_only:
                        state = 'plugins' if plugins_passed else 'plugins_failed'
                    else:
                        if self.dry_run:
                            test_target = "$SAGE_ROOT/src/sage/misc/a*.py"
                            # TODO: Remove
                            test_target = "$SAGE_ROOT/src/sage/doctest/*.py"
                        else:
                            test_target = "--all --long"
                        if self.config['parallelism'] > 1:
                            test_cmd = "-tp %s" % self.config['parallelism']
                        else:
                            test_cmd = "-t"
                        do_or_die("$SAGE_ROOT/sage %s %s" % (test_cmd,
                                                             test_target))
                        t.finish("Tests")
                        state = 'tested'

                        if not plugins_passed:
                            state = 'tests_passed_plugins_failed'

        except (urllib2.HTTPError, socket.error, ConfigException):
            # Don't report failure because the network/trac died...
            print
            self.write_log('network failure... skip this ticket', LOG_MAIN)
            t.print_all()
            traceback.print_exc()
            # Don't try this again for at least an hour.
            self.to_skip[ticket['id']] = time.time() + 60 * 60
            state = 'network_error'
        except SkipTicket, exn:
            self.to_skip[ticket['id']] = time.time() + exn.seconds_till_retry
            state = 'skipped'
            msg = "Skipping #{} for {} seconds {}"
            self.write_log(msg.format(ticket['id'],
                                      exn.seconds_till_retry, exn),
                           LOG_MAIN)
        except Exception:
            traceback.print_exc()
        except:
            # Don't try this again for a while.
            self.to_skip[ticket['id']] = time.time() + 12 * 60 * 60
            raise

        for _ in range(5):
            try:
                self.write_log("Reporting #{} with status {}".format(ticket['id'], status[state]),
                               LOG_MAIN)
                self.report_ticket(ticket, status=status[state], log=log,
                                   plugins=plugins_results,
                                   dry_run=self.dry_run)
                self.write_log("Done reporting #{}".format(ticket['id']), LOG_MAIN)
                break
            except IOError:
                traceback.print_exc()
                time.sleep(self.config['idle'])
        else:
            self.write_log("Error reporting #{}".format(ticket['id']), LOG_MAIN)
        maybe_temp_root = os.environ['SAGE_ROOT']
        if maybe_temp_root.endswith("-sage-git-temp-%s" % ticket['id']):
            shutil.rmtree(maybe_temp_root)
        return status[state]

    def check_spkg(self, spkg):
        temp_dir = None
        try:
            if '#' in spkg:
                spkg = spkg.split('#')[0]
            basename = os.path.basename(spkg)
            temp_dir = tempfile.mkdtemp()
            local_spkg = os.path.join(temp_dir, basename)
            do_or_die("wget --progress=dot:mega -O %s %s" % (local_spkg, spkg))
            do_or_die("tar xf %s -C %s" % (local_spkg, temp_dir))

            print
            print "Sha1", basename, sha1file(local_spkg)
            print
            print "Checking repo status."
            do_or_die("cd %s; hg diff; echo $?" % local_spkg[:-5])
            print
            print
            print "Comparing to previous spkg."

            # Compare to the current version.
            base = basename.split('-')[0]  # the reset is the version
            old_path = old_url = listing = None
            if False:
                # There seems to be a bug...
                #  File "/data/sage/sage-5.5/local/lib/python2.7/site-packages/pexpect.py", line 1137, in which
                #      if os.access (filename, os.X_OK) and not os.path.isdir(f):

                import pexpect
                p = pexpect.spawn("%s/sage" % self.sage_root,
                                  ['-i', '--info', base])
                while True:
                    index = p.expect([
                        r"Found package %s in (\S+)" % base,
                        r">>> Checking online list of (\S+) packages.",
                        r">>> Found (%s-\S+)" % base,
                        r"Error: could not find a package"])
                    if index == 0:
                        old_path = "$SAGE_ROOT/" + p.match.group(1)
                        break
                    elif index == 1:
                        listing = p.match.group(2)
                    elif index == 2:
                        old_url = "http://www.sagemath.org/packages/%s/%s.spkg" % (listing, p.match.group(1))
                        break
                    else:
                        print "No previous match."
                        break
            else:
                p = subprocess.Popen(r"%s/sage -i --info %s" % (self.sage_root, base),
                                     shell=True, stdout=subprocess.PIPE)
                for line in p.communicate()[0].split('\n'):
                    m = re.match(r"Found package %s in (\S+)" % base, line)
                    if m:
                        old_path = os.path.join(self.sage_root, m.group(1))
                        break
                    m = re.match(r">>> Checking online list of (\S+) packages.", line)
                    if m:
                        listing = m.group(1)
                    m = re.match(r">>> Found (%s-\S+)" % base, line)
                    if m:
                        old_url = "http://www.sagemath.org/packages/%s/%s.spkg" % (listing, m.group(1))
                        break
                if not old_path and not old_url:
                    print "Unable to locate existing package %s." % base

            if old_path is not None and old_path.startswith('/attachment/'):
                old_url = 'git://trac.sagemath.org/sage_trac' + old_path
            if old_url is not None:
                old_basename = os.path.basename(old_url)
                old_path = os.path.join(temp_dir, old_basename)
                if not os.path.exists(old_path):
                    do_or_die("wget --progress=dot:mega %s -O %s" % (old_url,
                                                                     old_path))
            if old_path is not None:
                old_basename = os.path.basename(old_path)
                if old_basename == basename:
                    print "PACKAGE NOT RENAMED"
                else:
                    do_or_die("tar xf %s -C %s" % (old_path, temp_dir))
                    print '\n\n', '-' * 20
                    do_or_die("diff -N -u -r -x src -x .hg %s/%s %s/%s; echo $?" % (temp_dir, old_basename[:-5], temp_dir, basename[:-5]))
                    print '\n\n', '-' * 20
                    do_or_die("diff -q -r %s/%s/src %s/%s/src; echo $?" % (temp_dir, old_basename[:-5], temp_dir, basename[:-5]))

            print
            print "-" * 20
            if old_path:
                do_or_die("head -n 100 %s/SPKG.txt" % local_spkg[:-5])
            else:
                do_or_die("cat %s/SPKG.txt" % local_spkg[:-5])

        finally:
            if temp_dir and os.path.exists(temp_dir):
                shutil.rmtree(temp_dir)

    def report_ticket(self, ticket, status, log, plugins=(),
                      dry_run=False, pending_status=None):
        """
        Report about a ticket.
        """
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
            'patchbot_version': self._version,
        }
        if pending_status:
            report['pending_status'] = pending_status
        try:
            tags = [describe_branch('patchbot/base', tag_only=True),
                    describe_branch('patchbot/ticket_upstream', tag_only=True)]
            report['base'] = ticket_base = sorted(tags, compare_version)[-1]
            report['git_base'] = self.git_commit('patchbot/base')
            report['git_base_human'] = describe_branch('patchbot/base')
            if ticket['id'] != 0:
                report['git_branch'] = ticket.get('git_branch', None)
                report['git_log'] = subprocess.check_output(['git', 'log', '--oneline', '%s..patchbot/ticket_upstream' % ticket_base]).strip().split('\n')
                # If apply failed, we don't want to be stuck in an infinite loop.
                report['git_commit'] = self.git_commit('patchbot/ticket_upstream')
                report['git_commit_human'] = describe_branch('patchbot/ticket_upstream')
                report['git_merge'] = self.git_commit('patchbot/ticket_merged')
                report['git_merge_human'] = describe_branch('patchbot/ticket_merged')
            else:
                report['git_branch'] = self.config['base_branch']
                report['git_log'] = []
                report['git_commit'] = report['git_merge'] = report['git_base']
        except Exception:
            traceback.print_exc()

        if status != 'Pending':
            self.write_log("#{}: {}{}".format(ticket['id'], status,
                                              " dry_run" if dry_run else ""),
                           [LOG_MAIN, LOG_MAIN_SHORT])

        print "REPORT"
        import pprint
        pprint.pprint(report)
        print ticket['id'], status
        fields = {'report': json.dumps(report)}
        if os.path.exists(log):
            files = [('log', 'log', bz2.compress(open(log).read()))]
        else:
            files = []
        if not dry_run or status == 'Pending':
            print post_multipart("%s/report/%s" % (self.server, ticket['id']), fields, files)

    def git_commit(self, branch):
        return git_commit(self.sage_root, branch)


def main(args):
    """
    Most configuration is done in the config file, which is reread between
    each ticket for live configuration of the patchbot.
    """
    global conf
    parser = OptionParser()
    parser.add_option("--config", dest="config",
                      help="specify the config file")
    parser.add_option("--sage-root", dest="sage_root",
                      default=os.environ.get('SAGE_ROOT'),
                      help="specify another sage root directory")
    parser.add_option("--server", dest="server",
                      default="http://patchbot.sagemath.org/",
                      help="specify another patchbot server adress")
    parser.add_option("--count", dest="count", default=1000000)
    parser.add_option("--ticket", dest="ticket", default=None,
                      help="test only a list of tickets, for example '12345,19876'")
    parser.add_option("--list", dest="list", action="store_true", default=False,
                      help="only write informations about tickets "
                           "that would be tested in the form: "
                           "[ticket id] [rating] [ticket title]")
    parser.add_option("--skip-base", action="store_true", dest="skip_base",
                      default=False,
                      help="whether to check that the base is errorless")
    parser.add_option("--dry-run", action="store_true", dest="dry_run",
                      default=False)
    parser.add_option("--plugin-only", action="store_true", dest="plugin_only",
                      default=False,
                      help="run the patchbot in plugin-only mode")
    parser.add_option("--cleanup", action="store_true", dest="cleanup",
                      default=False,
                      help="whether to cleanup the temporary files")
    parser.add_option("--safe-only", action="store_true", dest="safe_only",
                      default=True,
                      help="whether to run the patchbot in safe-only mode")
    parser.add_option("--interactive", action="store_true", dest="interactive",
                      default=False)

    (options, args) = parser.parse_args(args)

    conf_path = options.config and os.path.abspath(options.config)
    if options.ticket:
        tickets = [int(t) for t in options.ticket.split(',')]
        count = len(tickets)
    else:
        tickets = None
        count = int(options.count)

    patchbot = Patchbot(os.path.abspath(options.sage_root), options.server,
                        conf_path, dry_run=options.dry_run,
                        plugin_only=options.plugin_only, options=options)
    conf = patchbot.get_config()

    if options.list:
        # the option "--list" allows to see tickets that will be tested
        patchbot.get_one_ticket(verbose=1)
        sys.exit(0)

    if options.sage_root == os.environ.get('SAGE_ROOT'):
        print "WARNING: Do not use this copy of sage while the patchbot is running."
    ensure_free_space(options.sage_root)

    if conf['use_ccache']:
        do_or_die("'%s'/sage -i ccache" % options.sage_root, exn_class=ConfigException)
        # If we rebuild the (same) compiler we still want to share the cache.
        os.environ['CCACHE_COMPILERCHECK'] = '%compiler% --version'

    if not conf['skip_base']:
        patchbot.check_base()

        def good(report):
            return report['machine'] == conf['machine'] and report['status'] == 'TestsPassed'
        if options.plugin_only or not any(good(report) for report in patchbot.current_reports(0)):
            res = patchbot.test_a_ticket(0)
            if res not in ('TestsPassed', 'PluginOnly'):
                print "\n\n"
                print "Current base:", conf['base_repo'], conf['base_branch']
                if not options.interactive:
                    print "Failing tests in your base install: exiting."
                    sys.exit(1)
                while True:
                    print "Failing tests in your base install: %s. Continue anyways? [y/N] " % res
                    try:
                        ans = sys.stdin.readline().lower().strip()
                    except IOError:
                        # Might not be interactive.
                        print "Non interactive, not continuing."
                        ans = 'n'
                    if ans == '' or ans[0] == 'n':
                        sys.exit(1)
                    elif ans[0] == 'y':
                        break

    for k in xrange(count):
        if options.cleanup:
            for path in glob.glob(os.path.join(tempfile.gettempdir(),
                                               "*%s*" % temp_build_suffix)):
                patchbot.write_log("Cleaning up {}".format(path),
                                   [LOG_MAIN, LOG_MAIN_SHORT])
                shutil.rmtree(path)
        try:
            if tickets:
                ticket = tickets.pop(0)
            else:
                ticket = None
            conf = patchbot.reload_config()
            if check_time_of_day(conf['time_of_day']):
                if not patchbot.check_base():
                    patchbot.test_a_ticket(0)
                patchbot.test_a_ticket(ticket)
            else:
                patchbot.write_log("Idle.", [LOG_MAIN, LOG_MAIN_SHORT])
                time.sleep(conf['idle'])
        except Exception:
            traceback.print_exc()
            time.sleep(conf['idle'])

if __name__ == '__main__':
    # allow this script to serve as a single entry point for bots and
    # the server
    args = list(sys.argv)
    if len(args) > 1 and args[1] == '--serve':
        del args[1]
        from serve import main
    main(args)
