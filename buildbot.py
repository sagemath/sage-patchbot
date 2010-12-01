import re, os, sys, subprocess, time, traceback
import bz2, urllib2, json
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

def get_ticket(server, **conf):
    query = "raw"
    if 'trusted_authors' in conf:
        query += "&authors=" + ':'.join(conf['trusted_authors'])
    handle = urllib2.urlopen(server + "/ticket/?" + query)
    all = json.load(handle)
    handle.close()
    if 'trusted_authors' in conf:
        all = filter_on_authors(all, conf['trusted_authors'])
    all = filter(lambda x: x[0], ((rate_ticket(t, **conf), t) for t in all))
    all.sort()
    if all:
        return all[-1][1]

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
    redundancy = (100,)
    for reports in current_reports(ticket):
        redundancy = min(redundancy, compare_machines(reports['machine'], conf['machine']))
    if not redundancy[-1]:
        return # already did this one
    return redundancy, rating, -int(ticket['id'])

def report_ticket(server, ticket, status, base, machine, log):
    print ticket['id'], status
    report = {
        'status': status,
        'patches': ticket['patches'],
        'spkgs': ticket['spkgs'],
        'base': base,
        'machine': machine,
        'time': time.strftime('%Y-%m-%d %H:%M:%S %z'),
    }
    fields = {'report': json.dumps(report)}
    files = [('log', 'log', bz2.compress(open(log).read()))]
    print post_multipart("%s/report/%s" % (server, ticket['id']), fields, files)

class Tee:
    def __init__(self, filepath):
        self.filepath = filepath
        
    def __enter__(self):
        self._saved = os.dup(sys.stdout.fileno()), os.dup(sys.stderr.fileno())
        self.tee = subprocess.Popen(["tee", self.filepath], stdin=subprocess.PIPE)
        os.dup2(self.tee.stdin.fileno(), sys.stdout.fileno())
        os.dup2(self.tee.stdin.fileno(), sys.stderr.fileno())
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is not None:
            traceback.print_exc()
        self.tee.stdin.close()
        os.dup2(self._saved[0], sys.stdout.fileno())
        os.dup2(self._saved[1], sys.stderr.fileno())
        self.tee.wait()
        return False

def test_a_ticket(sage_root, server, idle):
    
    p = subprocess.Popen([os.path.join(sage_root, 'sage'), '-v'], stdout=subprocess.PIPE)
    if p.wait():
        raise ValueError, "Invalid sage_root='%s'" % sage_root
    version_info = p.stdout.read()
    base = re.match(r'Sage Version ([\d.]+)', version_info).groups()[0]
    ticket = get_ticket(base=base, server=server, **conf)
    if not ticket:
        print "No more tickets."
        time.sleep(idle)
        return
    print "\n" * 2
    print "=" * 30, ticket['id'], "=" * 30
    print ticket['title']
    print "\n" * 2
    status = 'started'
    log_dir = sage_root + "/logs"
    if not os.path.exists(log_dir):
        os.mkdir(log_dir)
    log = '%s/%s-log.txt' % (log_dir, ticket['id'])
    try:
        with Tee(log):
            pull_from_trac(sage_root, ticket['id'], force=True)
            status = 'applied'
            do_or_die('sage -b %s' % ticket['id'])
            status = 'built'
            do_or_die('sage -t %s/devel/sage-%s/sage/rings/integer.pyx' % (sage_root, ticket['id']))
            #do_or_die('sage -testall')
            status = 'tested'
    except Exception:
        traceback.print_exc()
    report_ticket(server, ticket, status=status, base=base, machine=conf['machine'], log=log)

if __name__ == '__main__':

    parser = OptionParser()
    parser.add_option("--config", dest="config")
    parser.add_option("--server", dest="server")
    parser.add_option("--sage", dest="sage_root")
    parser.add_option("--idle", dest="idle", default=300)
    (options, args) = parser.parse_args()
    
    unicode_conf = json.load(open(options.config))
    conf = {}
    for key, value in unicode_conf.items():
        conf[str(key)] = value
    del options.config

    if len(args) > 0:
        count = int(args[0])
    else:
        count = 1000000
    for _ in range(count):
        test_a_ticket(**options.__dict__)
