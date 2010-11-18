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
    for reports in db.reports.find({'ticket': ticket['id'], 'base': conf['base']}):
        redundancy = min(redundancy, compare_machines(reports['machine'], conf['machine']))
    if not redundancy[-1]:
        return # already did this one
    return redundancy, rating, -int(ticket['id'])

def report_ticket(ticket_id, status, **conf):
    print ticket_id, status
    report = {'ticket': ticket_id, 'status': status}
    report['base'] = conf['base']
    report['machine'] = conf['machine']
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
    'base': '4.5.3',
    'machine': machine,
    'bonus': {
        'robertwb': 50,
        'was': 10,
    }
}
#print get_ticket(trusted_authors=['robertwb', 'was', 'cremona', 'burcin'], base='4.6', machine=machine, bonus={'robertwb': 10})
while True:
    status = 'started'
    ticket = get_ticket(**conf)
    try:
        pull_from_trac('/Users/robertwb/sage/current', ticket['id'], force=True)
        status = 'applied'
        do_or_die('sage -b %s' % ticket['id'])
        status = 'built'
        do_or_die('sage -t /Users/robertwb/sage/current/devel/sage/sage/rings/integer.pyx')
        #do_or_die('sage -testall')
        status = 'tested'
    except Exception:
        import traceback
        traceback.print_exc()
    report_ticket(ticket['id'], status=status, **conf)
