import sys, bz2, json, traceback, re
from cStringIO import StringIO
from optparse import OptionParser
from flask import Flask, render_template, make_response, request, Response
import pymongo
import trac
import buildbot
import db

from db import tickets, logs
from buildbot import current_reports

app = Flask(__name__)

@app.route("/reports")
def reports():
    pass

@app.route("/")
@app.route("/ticket")
@app.route("/ticket/")
def ticket_list():
    authors = None
    if 'query' in request.args:
        query = json.loads(request.args.get('query'))
    else:
        query = {'status': 'needs_review'}
        if 'authors' in request.args:
            authors = request.args.get('authors').split(':')
            query['authors'] = {'$in': authors}
    if 'order' in request.args:
        order = request.args.get('order')
    else:
        order = 'id'
    if 'base' in request.args:
        base = request.args.get('base')
        if base == 'all':
            base = None
    else:
        base = global_base
    if 'author' in request.args:
        query['authors'] = request.args.get('author')
    if 'participant' in request.args:
        query['participants'] = request.args.get('participant')
    all = buildbot.filter_on_authors(tickets.find(query).sort(order), authors)
    if 'raw' in request.args:
        if 'pretty' in request.args:
            indent = 4
        else:
            indent = None
        response = make_response(json.dumps(list(all), default=lambda x: None, indent=indent))
        response.headers['Content-type'] = 'text/plain'
        return response
    summary = dict((key, 0) for key in status_order)
    def preprocess(all):
        for ticket in all:
            ticket['report_count'], ticket['report_status'] = get_ticket_status(ticket, base)
            if 'reports' in ticket:
                ticket['pending'] = len([r for r in ticket['reports'] if r['status'] == 'Pending'])
            summary[ticket['report_status']] += 1
            yield ticket
    ticket0 = db.lookup_ticket(0)
    versions = set(report['base'] for report in ticket0['reports'])
    versions = [(v, get_ticket_status(ticket0, v)) for v in sorted(versions)]
    return render_template("ticket_list.html", tickets=preprocess(all), summary=summary, base=base, base_status=get_ticket_status(db.lookup_ticket(0), base), versions=versions)

def format_patches(ticket, patches, good_patches=None):
    def note(patch):
        if good_patches is None or patch in good_patches:
            return ""
        else:
            return "<span style='color: red'>(mismatch)</span>"
    return ("<ol><li>" 
        + "\n<li>".join("<a href='%s'>%s</a> %s" % (trac.get_patch_url(ticket, patch, raw=False), patch, note(patch)) for patch in patches) 
        + ("" if (good_patches is None or len(patches) >= len(good_patches)) else "<li><span style='color: red'>(missing)</span>")
        + "</ol>")

@app.route("/ticket/<int:ticket>/")
def render_ticket(ticket):
    try:
        info = trac.scrape(ticket, db=db, force='force' in request.args)
    except:
        info = tickets.find_one({'id': ticket})
    if 'kick' in request.args:
        info['retry'] = True
        db.save_ticket(info)        
    if 'reports' in info:
        info['reports'].sort(lambda a, b: -cmp(a['time'], b['time']))
    else:
        info['reports'] = []
    def format_info(info):
        new_info = {}
        for key, value in info.items():
            if key == 'patches':
                new_info['patches'] = format_patches(ticket, value)
            elif key == 'reports' or key == 'pending':
                pass
            elif key == 'depends_on':
                new_info[key] = ', '.join("<a href='/ticket/%s'>%s</a>" % (a, a) for a in value)
            elif key == 'authors':
                new_info[key] = ', '.join("<a href='/ticket/?author=%s'>%s</a>" % (a,a) for a in value)
            elif key == 'participants':
                new_info[key] = ', '.join("<a href='/ticket/?participant=%s'>%s</a>" % (a,a) for a in value)
            elif isinstance(value, list):
                new_info[key] = ', '.join(value)
            elif key not in ('id', '_id'):
                new_info[key] = value
        return new_info
    def preprocess_reports(all):
        for item in all:
            if 'patches' in item:
                item['patch_list'] = format_patches(ticket, item['patches'], info['patches'])
            if item['base'] != base:
                item['base'] = "<span style='color: red'>%s</span>" % item['base']
            if 'time' in item:
                item['log'] = log_name(info['id'], item)
            yield item
    return render_template("ticket.html", reports=preprocess_reports(info['reports']), ticket=ticket, info=format_info(info), status=get_ticket_status(info, base=base)[1])

@app.route("/ticket/<int:ticket>/status.png")
def render_ticket_status(ticket):
    try:
        info = trac.scrape(ticket, db=db)
    except:
        info = tickets.find_one({'id': ticket})
    status = get_ticket_status(info, base=base)[1]
    response = make_response(open('images/%s-blob.png' % status_colors[status]).read())
    response.headers['Content-type'] = 'image/png'
    return response

def get_or_set(ticket, key, default):
    if key in ticket:
        value = ticket[key]
    else:
        value = ticket[key] = default
    return value

@app.route("/report/<int:ticket_id>", methods=['POST'])
def post_report(ticket_id):
    try:
        ticket = db.lookup_ticket(ticket_id)
        if ticket is None:
            ticket = trac.scrape(ticket_id)
        if 'reports' not in ticket:
            ticket['reports'] = []
        report = json.loads(request.form.get('report'))
        assert isinstance(report, dict)
        for fld in ['status', 'patches', 'spkgs', 'base', 'machine', 'time']:
            assert fld in report
        buildbot.prune_pending(ticket, report['machine'])
        ticket['reports'].append(report)
        if report['status'] != 'Pending':
            db.logs.put(request.files.get('log'), _id=log_name(ticket_id, report))
        if 'retry' in ticket:
            ticket['retry'] = False
        db.save_ticket(ticket)
        return "ok"
    except:
        traceback.print_exc()
        return "error"

def log_name(ticket_id, report):
    return "/log/%s/%s/%s" % (ticket_id, '/'.join(report['machine']), report['time'])


def shorten(lines):
    timing = re.compile(r'\s*\[\d+\.\d* s\]\s*$')
    skip = re.compile(r'(sage -t.*\(skipping\))|(byte-compiling)|(copying)|(\S+: \d+% \(\d+ of \d+\))$')
    gcc = re.compile('(gcc)|(g\+\+)')
    prev = None
    for line in StringIO(lines):
        if skip.match(line):
            pass
        elif prev is None:
            prev = line
        elif prev.startswith('sage -t') and timing.match(line):
            prev = None
        elif prev.startswith('python `which cython`') and '-->' in line:
            prev = None
        elif gcc.match(prev) and (gcc.match(line) or line.startswith('Time to execute')):
            prev = line
        else:
            yield prev
            prev = line

    if prev is not None:
        yield prev

@app.route("/log/<path:log>")
def get_log(log):
    path = "/log/" + log
    if not logs.exists(path):
        data = "No such log!"
    else:
        data = bz2.decompress(logs.get(path).read())
    if 'short' in request.args:
        response = Response(shorten(data), direct_passthrough=True)
    else:
        response = make_response(data)
    response.headers['Content-type'] = 'text/plain'
    return response

status_order = ['New', 'ApplyFailed', 'BuildFailed', 'TestsFailed', 'TestsPassed', 'Pending', 'Spkg']
# TODO: cleanup old records
# status_order += ['started', 'applied', 'built', 'tested']

status_colors = {
    'New'        : 'white',
    'ApplyFailed': 'red',
    'BuildFailed': 'red',
    'TestsFailed': 'yellow',
    'TestsPassed': 'green',
    'Pending'    : 'white',
    'Spkg'    : 'purple',
}

@app.route("/blob/<status>")
def status_image(status):
    response = make_response(open('images/%s-blob.png' % status_colors[status]).read())
    response.headers['Content-type'] = 'image/png'
    return response

def get_ticket_status(ticket, base=None):
    all = current_reports(ticket, base=base)
    if len(all):
        index = min(status_order.index(report['status']) for report in all)
        return len(all), status_order[index]
    elif ticket['spkgs'] or not ticket['patches']:
        return 0, 'Spkg'
    else:
        return 0, 'New'
    
if __name__ == '__main__':

    parser = OptionParser()
    parser.add_option("-b", "--base", dest="base")
    parser.add_option("-p", "--port", dest="port")
    (options, args) = parser.parse_args()

    global_base = base = options.base
    app.run(debug=True, host="0.0.0.0", port=int(options.port))
