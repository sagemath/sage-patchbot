import re, os, sys, subprocess, time, traceback
import bz2, urllib2, urllib, json
from optparse import OptionParser

from http_post_file import post_multipart

from trac import scrape, do_or_die, pull_from_trac

def filter_on_authors(tickets, authors):
    if authors is not None:
        authors = set(authors)
    for ticket in tickets:
        if authors is None or set(ticket['authors']).issubset(authors):
            yield ticket

def current_reports(ticket, base=None):
    if 'reports' not in ticket:
        return []
    return filter(lambda report: (ticket['patches'] == report['patches'] and
                                  ticket['spkgs'] == report['spkgs'] and
                                  (base is None or base == report['base'])),
                  ticket['reports'])

def contains_any(key, values):
    clauses = [{'key': value} for value in values]
    return {'$or': clauses}

def get_ticket(server, return_all=False, **conf):
    query = "raw"
    if 'trusted_authors' in conf:
        query += "&authors=" + ':'.join(conf['trusted_authors'])
    try:
        handle = urllib2.urlopen(server + "/ticket/?" + query)
        all = json.load(handle)
        handle.close()
    except:
        traceback.print_exc()
        return
    if 'trusted_authors' in conf:
        all = filter_on_authors(all, conf['trusted_authors'])
    all = filter(lambda x: x[0], ((rate_ticket(t, **conf), t) for t in all))
    all.sort()
    if return_all:
        return all
    if all:
        return all[-1]

def lookup_ticket(server, id):
    url = server + "/ticket/?" + urllib.urlencode({'raw': True, 'query': json.dumps({'id': id})})
    res = json.load(urllib2.urlopen(url))
    if res:
        return res[0]
    else:
        return scrape(id)

def compare_machines(a, b):
    if isinstance(a, dict) or isinstance(b, dict):
        # old format, remove
        return (1,)
    else:
        diff = [x != y for x, y in zip(a, b)]
        if len(a) != len(b):
            diff.append(1)
        return diff

def rate_ticket(ticket, **conf):
    rating = 0
    if ticket['spkgs']:
        return # can't handle these yet
    for author in ticket['authors']:
        if author not in conf['trusted_authors']:
            return
        rating += conf['bonus'].get(author, 0)
    for participant in ticket['participants']:
        rating += conf['bonus'].get(participant, 0) # doubled for authors
    rating += len(ticket['participants'])
    # TODO: remove condition
    if 'component' in ticket:
        rating += conf['bonus'].get(ticket['component'], 0)
    rating += conf['bonus'].get(ticket['priority'], 0)
    rating += conf['bonus'].get(ticket['id'], 0)
    redundancy = (100,)
    prune_pending(ticket)
    for reports in current_reports(ticket, base=conf['base']):
        redundancy = min(redundancy, compare_machines(reports['machine'], conf['machine']))
    if not redundancy[-1]:
        return # already did this one
    return redundancy, rating, -int(ticket['id'])

DATE_FORMAT = '%Y-%m-%d %H:%M:%S %z'
def datetime():
    return time.strftime(DATE_FORMAT)

def parse_datetime(s):
    # The one thing Python can't do is parse dates...
    return time.mktime(time.strptime(s[:-5].strip(), DATE_FORMAT[:-3])) + 60*int(s[-5:].strip())

def prune_pending(ticket, machine=None):
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
            elif now - t > 6 * 60 * 60:
                reports.remove(report)
    return reports

def report_ticket(server, ticket, status, base, machine, log):
    print ticket['id'], status
    report = {
        'status': status,
        'patches': ticket['patches'],
        'spkgs': ticket['spkgs'],
        'base': base,
        'machine': machine,
        'time': datetime(),
    }
    fields = {'report': json.dumps(report)}
    if status != 'Pending':
        files = [('log', 'log', bz2.compress(open(log).read()))]
    else:
        files = []
    try:
        print post_multipart("%s/report/%s" % (server, ticket['id']), fields, files)
    except:
        traceback.print_exc()

class Tee:
    def __init__(self, filepath, time=False):
        self.filepath = filepath
        self.time = time
        
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
        os.dup2(self._saved[0], sys.stdout.fileno())
        os.dup2(self._saved[1], sys.stderr.fileno())
        self.tee.wait()
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
    'started': 'ApplyFailed',
    'applied': 'BuildFailed',
    'built'  : 'TestsFailed',
    'tested' : 'TestsPassed',
}

def get_base(sage_root):
    p = subprocess.Popen([os.path.join(sage_root, 'sage'), '-v'], stdout=subprocess.PIPE)
    if p.wait():
        raise ValueError, "Invalid sage_root='%s'" % sage_root
    version_info = p.stdout.read()
    return re.search(r'Sage Version ([\d.]+)', version_info).groups()[0]
    

def test_a_ticket(sage_root, server, idle, parallelism, ticket=None, nodocs=False):
    base = get_base(sage_root)
    if ticket is None:
        ticket = get_ticket(base=base, server=server, **conf)
    else:
        ticket = scrape(int(ticket))
    if not ticket:
        print "No more tickets."
        time.sleep(idle)
        return
    rating, ticket = ticket
    print "\n" * 2
    print "=" * 30, ticket['id'], "=" * 30
    print ticket['title']
    print "score", rating
    print "\n" * 2
    log_dir = sage_root + "/logs"
    if not os.path.exists(log_dir):
        os.mkdir(log_dir)
    log = '%s/%s-log.txt' % (log_dir, ticket['id'])
    report_ticket(server, ticket, status='Pending', base=base, machine=conf['machine'], log=None)
    try:
        with Tee(log, time=True):
            t = Timer()
            start_time = time.time()
            state = 'started'
            os.environ['MAKE'] = "make -j%s" % parallelism
            os.environ['SAGE_ROOT'] = sage_root
            pull_from_trac(sage_root, ticket['id'], force=True)
            state = 'applied'
            t.finish("Apply")
            os.system('$SAGE_ROOT/sage -coverageall')
            t.finish("Coverage")
            do_or_die('$SAGE_ROOT/sage -b %s' % ticket['id'])
            t.finish("Build")
            if not nodocs:
                do_or_die('$SAGE_ROOT/sage -docbuild --jsmath reference html')
                t.finish("DocBuild")
            state = 'built'
            test_dirs = ["$SAGE_ROOT/devel/sage-%s/%s" % (ticket['id'], dir) for dir in all_test_dirs]
            do_or_die("$SAGE_ROOT/sage -tp %s -sagenb %s" % (parallelism, ' '.join(test_dirs)))
            #do_or_die('sage -testall')
            state = 'tested'
            t.finish("Tests")
            print
            t.print_all()
    except Exception:
        traceback.print_exc()
    report_ticket(server, ticket, status=status[state], base=base, machine=conf['machine'], log=log)
    return status[state]

def get_conf(path):
    unicode_conf = json.load(open(path))
    conf = {}
    for key, value in unicode_conf.items():
        conf[str(key)] = value
    return conf

if __name__ == '__main__':

    parser = OptionParser()
    parser.add_option("--count", dest="count", default=1000000)
    parser.add_option("--ticket", dest="ticket", default=None)
    parser.add_option("--config", dest="config")
    parser.add_option("--server", dest="server")
    parser.add_option("--sage", dest="sage_root")
    parser.add_option("--idle", dest="idle", default=300)
    parser.add_option("--parallelism", dest="parallelism", default=3)
    parser.add_option("--list", dest="list", default=False)
    parser.add_option("--nodocs", dest="nodocs", default=False)
    (options, args) = parser.parse_args()

    conf_path = os.path.abspath(options.config)
    if options.ticket:
        tickets = [int(t) for t in options.ticket.split(',')]
        count = len(tickets)
    else:
        tickets = None
        count = int(options.count)
    params = dict(sage_root=options.sage_root, server=options.server, idle=options.idle, parallelism=options.parallelism, nodocs=options.nodocs)

    conf = get_conf(conf_path)
    if options.list:
        for score, ticket in get_ticket(base=get_base(options.sage_root), server=options.server, return_all=True, **conf):
            print score, ticket['id'], ticket['title']
            print ticket
            print
        sys.exit(0)
    
    clean = lookup_ticket(options.server, 0)
    def good(report):
        return report['machine'] == conf['machine'] and report['status'] == 'TestsPassed'
    if not any(good(report) for report in current_reports(clean, base=get_base(options.sage_root))):
        res = test_a_ticket(ticket=0, **params)
        if res != 'TestsPassed':
            print "\n\n"
            while True:
                print "Failing tests in your install: %s. Continue anyways? [y/N] " % res
                ans = sys.stdin.readline().lower().strip()
                if ans == '' or ans[0] == 'n':
                    sys.exit(1)
                elif ans[0] == 'y':
                    break

    for _ in range(count):
        if tickets:
            ticket = tickets.pop(0)
        else:
            ticket = None
        conf = get_conf(conf_path)
        test_a_ticket(ticket=ticket, **params)
    # TODO: periodically cleanup closed tickets
