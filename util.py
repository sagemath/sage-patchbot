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

def do_or_die(cmd):
    print cmd
    res = os.system(cmd)
    if res:
        raise Exception, "%s %s" % (res, cmd)

def extract_version(s):
    m = re.search(r'\d+(\.\d+)+(\.\w+)', s)
    if m:
        return m.group(0)

def compare_version(a, b):
    a += '.z'
    b += '.z'
    def maybe_int(s):
        try:
            return 1, int(s)
        except:
            return 0, s
    return cmp([maybe_int(v) for v in a.split('.')],
               [maybe_int(v) for v in b.split('.')])

def get_base(sage_root):
    p = subprocess.Popen([os.path.join(sage_root, 'sage'), '-v'], stdout=subprocess.PIPE)
    if p.wait():
        raise ValueError, "Invalid sage_root='%s'" % sage_root
    version_info = p.stdout.read()
    return re.search(r'Sage Version ([\d.]+\w*)', version_info).groups()[0]
