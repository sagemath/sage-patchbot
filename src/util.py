import os
import re
import subprocess

from dateutil import parser
from datetime import datetime
import pytz

# check_output for Python < 2.7

if "check_output" not in subprocess.__dict__:  # duck punch it in!
    def check_output(args):
        process = subprocess.Popen(args, stdout=subprocess.PIPE)
        output, _ = process.communicate()
        retcode = process.poll()
        if retcode:
            raise subprocess.CalledProcessError(retcode, args[0])
        return output
    subprocess.check_output = check_output

temp_build_suffix = "-sage-git-temp-"

DATE_FORMAT = '%Y-%m-%d %H:%M:%S %z'


def now_str():
    """
    Return the current day and time as a string.

    In [3]: now_str()
    Out[3]: '2015-07-23 09:00:08 +0200'
    """
    return datetime.now(pytz.utc).strftime(DATE_FORMAT)


def parse_datetime(s):
    """
    Return the number of second since epoch.

    a = '2015-07-23 09:00:08 +0200'
    In [4]: parse_datetime(a)

    In [6]: b = '2015-07-23T09:00:08+0200'
    In [7]: parse_datetime(b)
    """
    dt = parser.parse(s)
    epoch = datetime(1970, 1, 1, tzinfo=pytz.utc)
    return (dt - epoch).total_seconds()


def prune_pending(ticket, machine=None, timeout=None):
    """
    Remove pending reports from ``ticket.reports`` if ``machine`` is matched
    and ``report.time`` is longer than ``timeout`` old.

    ``timeout`` is currently set to 6 hours by default
    """
    if timeout is None:
        timeout = 6 * 60 * 60
    if 'reports' in ticket:
        reports = ticket['reports']
    else:
        return []
    now = datetime.now(pytz.utc)
    for report in list(reports):
        if report['status'] == 'Pending':
            t = parser.parse(report['time'])
            if report['machine'] == machine:
                reports.remove(report)
            elif (now - t).total_seconds() > timeout:
                reports.remove(report)
    return reports


def latest_version(reports):
    """
    Return newest report.base in reports.
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

        def first(x):
            if x in seen:
                return False
            else:
                seen.add(x)
                return True
    else:
        first = lambda x: True

    reports = list(ticket['reports'])
    reports.sort(key=lambda a: a['time'])

    if base == 'latest':
        base = latest_version(reports)

    def base_ok(report_base):
        return (not base or base == report_base
                or (newer and comparable_version(base) <=
                    comparable_version(report_base)))

    if ticket['id'] == 0:
        return [rep for rep in reports if base_ok(report['base'])]

    # git_commit is not set for ticket 0
    def filtre_fun(report):
        return (ticket.get('git_commit') == report.get('git_commit') and
                ticket['spkgs'] == report['spkgs'] and
                ticket['depends_on'] == report.get('deps', []) and
                base_ok(report['base']) and
                first(':'.join(report['machine'])))

    return [rep for rep in reports if filtre_fun(rep)]


def is_git(sage_root):
    """
    Return ``True`` if sage_root has a .git directory.

    This should now always be true.

    In [10]: is_git('/home/louis_de_funes/sage')
    Out[10]: True
    """
    return os.path.exists(sage_root + "/.git")


def git_commit(repo, branch):
    """
    Note: see almost the same function in trac.py

    In [16]: git_commit('/home/marlon_brando/sage', 'develop')
    Out[16]: '7eb8510dacf61b691664cd8f1d2e75e5d473e5a0'
    """
    ref = "refs/heads/{}".format(branch)
    try:
        return subprocess.check_output(["git", "--git-dir={}/.git".format(repo),
                                        "show-ref",
                                        "--verify", ref]).split()[0]
    except subprocess.CalledProcessError:
        return None


def do_or_die(cmd, exn_class=Exception):
    """
    Run a shell command and raise an exception in case of eventual failure.
    """
    print cmd
    res = os.system(cmd)
    if res:
        raise exn_class("{} {}".format(res, cmd))


def comparable_version(version):
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

    def maybe_int(s):
        try:
            return 1, int(s)
        except:
            return 0, s
    return [maybe_int(s) for s in version.split('.')]


def compare_version(a, b):
    """
    Compare two versions a and b.

    EXAMPLES::

    In [5]: compare_version('6.4.rc0','6.4')
    Out[5]: -1

    In [6]: compare_version('6.4.rc0','6.4.beta2')
    Out[6]: 1

    In [7]: compare_version('6.4','6.3')
    Out[7]: 1

    In [8]: compare_version('6.3','6.3.1')
    Out[8]: -1
    """
    return cmp(comparable_version(a), comparable_version(b))


def get_version(sage_root):
    """
    Get the sage version.

    The expected result is a string of the shape '6.6' or '6.6.rc1'

    This is found in the VERSION.txt file.

    In [9]: get_version('/home/paul_gauguin/sage')
    Out[9]: '6.6.rc0'
    """
    sage_version = open(os.path.join(sage_root, 'VERSION.txt')).read()
    return sage_version.split()[2].strip(',')


def describe_branch(branch, tag_only=False):
    """
    Return the latest tag of the branch or the full branch description.

    >>> describe_branch('develop', True)
    '6.6.rc1'
    """
    res = subprocess.check_output(['git', 'describe', '--tags',
                                   '--match', '[0-9].[0-9]*', branch]).strip()
    if tag_only:
        return res.split('-')[0]
    else:
        return res


def ensure_free_space(path):
    stats = os.statvfs(path)
    free = stats.f_bfree * stats.f_frsize
    if stats.f_bfree * stats.f_frsize < (4 << 30):
        msg = "Refusing to build with less than 4G free ({} bytes "
        msg += "available on {})"
        raise ConfigException(msg.format(free, path))


class ConfigException(Exception):
    """
    An exception to raise to abort the patchbot without implicating a ticket.
    """
    pass


class SkipTicket(Exception):
    """
    An exception to raise to abort this ticket without reporting
    failure or re-trying it again for a while.
    """
    def __init__(self, msg, seconds_till_retry=float('inf')):
        super(SkipTicket, self).__init__(msg)
        self.seconds_till_retry = seconds_till_retry
