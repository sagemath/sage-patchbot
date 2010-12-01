import re, os, sys, subprocess, time, traceback
import bz2

import pymongo
import db
from trac import scrape, do_or_die, pull_from_trac

def update_database(ticket_id):
    scrape(ticket_id)

def contains_any(key, values):
    clauses = [{'key': value} for value in values]
    return {'$or': clauses}

def get_ticket(**conf):
    query = {
        'status': 'needs_review',
        'patches': {'$ne': []},
        'spkgs': [],
    }
    if conf['trusted_authors']:
        query['authors'] = {'$in': conf['trusted_authors']}
#    print query
    all = [(rate_ticket(t, **conf), t) for t in db.tickets.find(query)]
    all = filter(lambda x: x[0], all)
    all.sort()
#    for data in all:
#        print data
    print all[-1]
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
    # TODO: component bonuses
    redundancy = (100,)
    query = {
        'ticket': ticket['id'],
        'base': conf['base'],
        'patches': ticket['patches'],
        'spkgs': ticket['spkgs'],
    }
    for reports in db.reports.find(query):
        redundancy = min(redundancy, compare_machines(reports['machine'], conf['machine']))
    if not redundancy[-1]:
        return # already did this one
    return redundancy, rating, -int(ticket['id'])

def report_ticket(ticket, status, base, machine, log):
    print ticket['id'], status
    report = {
        'ticket': ticket['id'],
        'status': status,
        'patches': ticket['patches'],
        'spkgs': ticket['spkgs'],
        'base': base,
        'machine': machine,
        'time': time.strftime('%Y-%m-%d %H:%M:%S %z'),
    }
    db.reports.save(report)
    post_log(report, log)

def log_name(report):
    return "/log/%s/%s/%s" % (report['ticket'], '/'.join(report['machine']), report['time'])

def post_log(report, log):
    data = bz2.compress(open(log).read())
    db.logs.put(data, _id=log_name(report))

if False:
    for id in range(8000, 9000):
        print id
        try:
            update_database(id)
        except:
            import traceback
            traceback.print_exc()

conf = {
    'trusted_authors': ['robertwb', 'was', 'cremona', 'burcin', 'mhansen'], 
    'machine': ('os-x', '10.6', '10.6.3', 'my-mac2'),
    'bonus': {
        'robertwb': 50,
        'was': 10,
    }
}

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

def test_a_ticket(sage_root):
    p = subprocess.Popen([os.path.join(sage_root, 'sage'), '-v'], stdout=subprocess.PIPE)
    if p.wait():
        raise ValueError, "Invalid sage_root='%s'" % sage_root
    version_info = p.stdout.read()
    base = re.match(r'Sage Version ([\d.]+)', version_info).groups()[0]
    ticket = get_ticket(base=base, **conf)
    print "\n" * 2
    print "=" * 30, ticket['id'], "=" * 30
    print ticket['title']
    print "\n" * 2
    status = 'started'
    log = '%s/devel/sage-%s/log.txt' % (sage_root, ticket['id'])
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
    report_ticket(ticket, status=status, base=base, machine=conf['machine'], log=log)

if __name__ == '__main__':

    if len(sys.argv) > 1:
        count = int(sys.argv[1])
    else:
        count = 1000000
    for _ in range(count):
        test_a_ticket('/Users/robertwb/sage/sage-4.6')
