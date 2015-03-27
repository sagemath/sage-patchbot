import os
import re
import subprocess
import time

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
    return time.strftime(DATE_FORMAT)


def parse_datetime(s):
    # The one thing Python can't do is parse dates...
    tz = 60 * 60 * int(s[-5:].strip()[:-2])
    return time.mktime(time.strptime(s[:-5].strip(), DATE_FORMAT[:-3])) + tz


def prune_pending(ticket, machine=None, timeout=None):
    """
    Remove pending reports from ``ticket.reports`` if ``machine`` is matched
    and ``report.time`` is longer than ``timeout`` old.
    """
    if timeout is None:
        timeout = 6 * 60 * 60
    if 'reports' in ticket:
        reports = ticket['reports']
    else:
        return []
    # TODO: is there a better way to handle time zones?
    now = time.time() + 60 * 60 * int(time.strftime('%z')[:-2])
    for report in list(reports):
        if report['status'] == 'Pending':
            t = parse_datetime(report['time'])
            if report['machine'] == machine:
                reports.remove(report)
            elif now - t > timeout:
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

    If unique, add only unique reports. If newer, filter out reports
    that are older than current base.
    """
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

    def filtre_fun(report):
        return (ticket.get('git_commit') == report.get('git_commit') and
                ticket['spkgs'] == report['spkgs'] and
                ticket['depends_on'] == report.get('deps', []) and
                base_ok(report['base']) and
                first(':'.join(report['machine'])))

    return filter(filtre_fun, reports)


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
    Note: see also the same function in trac.py
    """
    ref = "refs/heads/{}".format(branch)
    try:
        return subprocess.check_output(["git", "--git-dir=%s/.git" % repo,
                                        "show-ref",
                                        "--verify", ref]).split()[0]
    except subprocess.CalledProcessError:
        return None


def do_or_die(cmd, exn_class=Exception):
    """
    Run a shell command and report eventual failure.
    """
    print cmd
    res = os.system(cmd)
    if res:
        raise exn_class("%s %s" % (res, cmd))


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
