import re, os, sys, subprocess, time

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
    return tuple(a[key] != b[key] for key in ['os', 'distro', 'version', 'id'])

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

def report_ticket(ticket, status, base, machine):
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

if False:
    for id in range(8000, 9000):
        print id
        try:
            update_database(id)
        except:
            import traceback
            traceback.print_exc()

machine = {
    'os': 'os-x',
    'distro': '10.6',
    'version': '10.6.3',
    'id': 'my-mac',
}
conf = {
    'trusted_authors': ['robertwb', 'was', 'cremona', 'burcin', 'mhansen'], 
    'machine': machine,
    'bonus': {
        'robertwb': 50,
        'was': 10,
    }
}

def test_a_ticket(sage_root):
    p = subprocess.Popen([os.path.join(sage_root, 'sage'), '-v'], stdout=subprocess.PIPE)
    if p.wait():
        raise ValueError, "Invalid sage_root='%s'" % sage_root
    version_info = p.stdout.read()
    base = re.match(r'Sage Version ([\d.]+)', version_info).groups()[0]
    ticket = get_ticket(base=base, **conf)
    status = 'started'
    try:
        pull_from_trac(sage_root, ticket['id'], force=True)
        status = 'applied'
        do_or_die('sage -b %s' % ticket['id'])
        status = 'built'
        do_or_die('sage -t %s/devel/sage/sage/rings/integer.pyx' % sage_root)
        #do_or_die('sage -testall')
        status = 'tested'
    except Exception:
        import traceback
        traceback.print_exc()
    report_ticket(ticket, status=status, base=base, machine=conf['machine'])

if __name__ == '__main__':
    if len(sys.argv) > 1:
        count = int(sys.argv[1])
    else:
        count = sys.max_int
    for _ in range(count):
        test_a_ticket('/Users/robertwb/sage/sage-4.6')
