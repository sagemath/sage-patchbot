from __future__ import annotations

import os
import re
import subprocess

from datetime import datetime

temp_build_suffix = "-sage-git-temp-"
DATE_FORMAT = '%Y-%m-%d %H:%M:%S'


def date_parser(date_string: str):
    """
    Parse a datetime string into a datetime object.

    EXAMPLES::

        In [4]: date_parser('2015-07-23 09:00:08')
        Out[4]: datetime.datetime(2015, 7, 23, 9, 0, 8)
    """
    return datetime.strptime(date_string[:19], DATE_FORMAT)


def now_str() -> str:
    """
    Return the current day and time as a string.

    in the UTC timezone

    EXAMPLES::

        In [3]: now_str()
        Out[3]: '2015-07-23 09:00:08'
    """
    return datetime.utcnow().strftime(DATE_FORMAT)


def prune_pending(ticket, machine=None, timeout=None) -> list[dict]:
    """
    Remove pending reports from ``ticket.reports``.

    A pending report is removed if ``machine`` is matched
    or ``report.time`` is longer than ``timeout`` old.

    The ``timeout`` is currently set to 6 hours by default
    """
    if timeout is None:
        timeout = 6 * 60 * 60
    if 'reports' in ticket:
        reports = ticket['reports']
    else:
        return []
    now = datetime.utcnow()  # in the utc timezone
    for report in reports:
        if report['status'] == 'Pending':
            t = date_parser(report['time'])
            if report['machine'] == machine:
                reports.remove(report)
            elif (now - t).total_seconds() > timeout:
                reports.remove(report)
    return reports


def latest_version(reports: list):
    """
    Return the newest ``report.base`` in the given list of reports.
    """
    if reports:
        return max([r['base'] for r in reports], key=comparable_version)
    else:
        return None


def current_reports(ticket, base=None, unique=False, newer=False):
    """
    Return list of reports of the ticket optionally filtered by base.

    INPUT:

    - ``ticket`` -- dictionary

    - ``base`` -- can be set to 'latest', default ``None``

    - ``unique`` -- boolean, if ``True``, return just one report per machine

    - ``newer`` -- boolean, if ``True``, filter out reports that are older
      than the given base.

    OUTPUT:

    a list of reports
    """
    if newer and not base:
        raise ValueError('newer required but no base given')

    if 'reports' not in ticket:
        return []

    if unique:
        seen = set()

        def first(x) -> bool:
            if x in seen:
                return False
            else:
                seen.add(x)
                return True
    else:

        def first(x) -> bool:
            return True

    reports = list(ticket['reports'])
    reports.sort(key=lambda a: a['time'])

    if base == 'latest':
        base = latest_version(reports)

    def base_ok(report_base: str) -> bool:
        return (not base or base == report_base or
                (newer and comparable_version(base) <=
                 comparable_version(report_base)))

    if ticket['id'] == 0:
        return [rep for rep in reports if base_ok(rep['base'])]

    # git_commit is not set for ticket 0
    def filtre_fun(report: dict) -> bool:
        return (ticket.get('git_commit') == report.get('git_commit') and
                ticket['spkgs'] == report['spkgs'] and
                ticket['depends_on'] == report.get('deps', []) and
                base_ok(report['base']) and
                first(':'.join(report['machine'])))

    return [rep for rep in reports if filtre_fun(rep)]


def is_git(sage_root: str) -> bool:
    """
    Return ``True`` if sage_root has a .git directory.

    This should now always be true.

    EXAMPLES::

        In [10]: is_git('/home/louis_de_funes/sage')
        Out[10]: True
    """
    return os.path.exists(sage_root + "/.git")


def git_commit(repo: str, branch: str):
    """
    Note: see almost the same function in trac.py

    EXAMPLES::

        In [16]: git_commit('/home/marlon_brando/sage', 'develop')
        Out[16]: '7eb8510dacf61b691664cd8f1d2e75e5d473e5a0'
    """
    ref = "refs/heads/{}".format(branch)
    try:
        res = subprocess.check_output(["git",
                                       "--git-dir={}/.git".format(repo),
                                       "show-ref",
                                       "--verify", ref],
                                      universal_newlines=True)
        return res.split()[0]
    except subprocess.CalledProcessError:
        return None


def branch_updates_some_package() -> bool:
    """
    Does the ticket branch contains the update of some package ?
    """
    cmd = ["git", "diff", "--name-only",
           "patchbot/base..patchbot/ticket_merged"]
    for file in subprocess.check_output(cmd,
                                        universal_newlines=True).split('\n'):
        if not file:
            continue
        if file.startswith("build/pkgs") and file.endswith("checksums.ini"):
            msg = "Modified package: {}".format(file)
            print(msg)
            return True
    return False


def do_or_die(cmd: str, exn_class=Exception):
    """
    Run a shell command and raise an exception in case of eventual failure.
    """
    print(cmd)
    res = os.system(cmd)
    if res:
        raise exn_class("{} {}".format(res, cmd))


def comparable_version(version: str) -> list:
    """
    Convert a version into something comparable.

    EXAMPLES::

        In [2]: comparable_version('6.6.rc0')
        Out[2]: [(1, 6), (1, 6), (0, 'rc'), (1, 0), (0, 'z')]

        In [3]: comparable_version('6.6')
        Out[3]: [(1, 6), (1, 6), (0, 'z')]

        In [4]: comparable_version('6.6.beta4')
        Out[4]: [(1, 6), (1, 6), (0, 'beta'), (1, 4), (0, 'z')]
    """
    version = re.sub(r'([^.0-9])(\d+)', r'\1.\2', version) + '.z'

    def maybe_int(s: str) -> tuple:
        try:
            return 1, int(s)
        except ValueError:
            return 0, s
    return [maybe_int(s) for s in version.split('.')]


def get_sage_version(sage_root: str) -> str:
    """
    Get the sage version.

    The expected result is a string of the shape '6.6' or '6.6.rc1'

    This is found in the VERSION.txt file.

    EXAMPLES::

        In [9]: get_sage_version('/home/paul_gauguin/sage')
        Out[9]: '6.6.rc0'
    """
    sage_version = open(os.path.join(sage_root, 'VERSION.txt')).read()
    return sage_version.split()[2].strip(',')


def get_python_version(sage_cmd: str) -> str:
    """
    get the python version run by sage

    input: full path to the sage executable

    output: a string of the shape '3.9.2'
    """
    # res = subprocess.check_output([sage_cmd, "--python-version"])
    # return int(res[0])
    # code above for future use

    res = subprocess.run([sage_cmd, "--python", "--version"],
                         capture_output=True, text=True).stdout
    return res.strip().split(" ")[1]


def describe_branch(branch: str, tag_only=False):
    """
    Return the latest tag of the branch or the full branch description.

    EXAMPLES::

        >>> describe_branch('develop', True)
        '6.6.rc1'
    """
    res = subprocess.check_output(['git', 'describe', '--tags',
                                   '--match', '[0-9].[0-9]*', branch],
                                  universal_newlines=True)
    res = res.strip()
    if tag_only:
        return res.split('-')[0]
    else:
        return res


def ensure_free_space(path, N=4):
    """
    check that available free space is at least N Go
    """
    stats = os.statvfs(path)
    free = stats.f_bfree * stats.f_frsize
    if stats.f_bfree * stats.f_frsize < (N * 2**30):
        msg = ("Refusing to build with less than {:.2f}G free ({} bytes "
               "available on {})")
        raise ConfigException(msg.format(N, free, path))


class ConfigException(Exception):
    """
    An exception to raise to abort the patchbot without implicating a ticket.
    """


class TestsFailed(Exception):
    """
    Exception raised to indicate that the Sage tests failed or otherwise
    exited with an error status.
    """


class SkipTicket(Exception):
    """
    An exception to raise to abort this ticket without reporting
    failure or re-trying it again for a while.
    """
    def __init__(self, msg, seconds_till_retry=float('inf')):
        super(SkipTicket, self).__init__(msg)
        self.seconds_till_retry = seconds_till_retry
