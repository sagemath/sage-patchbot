import os, re, subprocess, time


DATE_FORMAT = '%Y-%m-%d %H:%M:%S %z'
def now_str():
    return time.strftime(DATE_FORMAT)

def parse_datetime(s):
    # The one thing Python can't do is parse dates...
    return time.mktime(time.strptime(s[:-5].strip(), DATE_FORMAT[:-3])) + 60*int(s[-5:].strip())

def prune_pending(ticket, machine=None, timeout=6*60*60):
    if 'reports' in ticket:
        reports = ticket['reports']
    else:
        return []
    # TODO: is there a better way to handle time zones?
    now = time.time() + 60 * int(time.strftime('%z'))
    for report in list(reports):
        if report['status'] == 'Pending':
            t = parse_datetime(report['time'])
            if report['machine'] == machine:
                reports.remove(report)
            elif now - t > timeout:
                reports.remove(report)
    return reports

def latest_version(reports):
    if reports:
        return max([r['base'] for r in reports], key=comparable_version)
    else:
        return None

def current_reports(ticket, base=None, unique=False, newer=False):
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
    reports.sort(lambda a, b: cmp(b['time'], a['time']))
    if base == 'latest':
        base = latest_version(reports)
    def base_ok(report_base):
        return (not base 
            or base == report_base
            or (newer and comparable_version(base) <= comparable_version(report_base)))
    return filter(lambda report: (ticket['patches'] == report['patches'] and
                                  ticket['spkgs'] == report['spkgs'] and
                                  ticket['depends_on'] == (report.get('deps') or []) and
                                  base_ok(report['base']) and
                                  first('/'.join(report['machine']))),
                      reports)

def is_git(sage_root):
    return os.path.exists(sage_root + "/.git")

def git_commit(repo, branch):
    ref = "refs/heads/%s"%branch
    try:
        return subprocess.check_output(["git", "--git_dir=%s/.git" % repo, "show-ref", "--quiet", "--verify", ref])
    except subprocess.CalledProcessError:
        return None

def do_or_die(cmd):
    print cmd
    res = os.system(cmd)
    if res:
        raise Exception, "%s %s" % (res, cmd)

def extract_version(s):
    m = re.search(r'\d+(\.\d+)+(\.\w+)', s)
    if m:
        return m.group(0)

def comparable_version(version):
    version = re.sub(r'([^.0-9])(\d+)', r'\1.\2', version) + '.z'
    def maybe_int(s):
        try:
            return 1, int(s)
        except:
            return 0, s
    return [maybe_int(s) for s in version.split('.')]

def compare_version(a, b):
    return cmp(comparable_version(a), comparable_version(b))

def get_base(sage_root):
    p = subprocess.Popen([os.path.join(sage_root, 'sage'), '-v'], stdout=subprocess.PIPE)
    if p.wait():
        raise ValueError, "Invalid sage_root='%s'" % sage_root
    version_info = p.stdout.read()
    return re.search(r'Sage Version ([\d.]+\w*)', version_info).groups()[0]
