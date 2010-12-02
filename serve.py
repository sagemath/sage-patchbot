import sys, bz2, json, traceback
from optparse import OptionParser
from flask import Flask, render_template, make_response, request
import pymongo
import trac
import buildbot
import db

from db import tickets, logs
from buildbot import current_reports

app = Flask(__name__)

@app.route("/")
@app.route("/ticket")
@app.route("/ticket/")
def ticket_list():
    query = {'status': 'needs_review'}
    if 'authors' in request.args:
        authors = request.args.get('authors').split(':')
        query['authors'] = {'$in': authors}
    else:
        authors = None
    all = buildbot.filter_on_authors(tickets.find(query).sort('id'), authors)
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
            summary[ticket['report_status']] += 1
            yield ticket
    return render_template("ticket_list.html", tickets=preprocess(all), summary=summary)

def format_patches(ticket, patches, good_patches=None):
    if good_patches is not None:
        print "len(patches) >= len(good_patches)", len(patches) , len(good_patches)
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
        info = trac.scrape(ticket)
    except:
        info = tickets.find_one({'id': ticket})
    if 'reports' in info:
        info['reports'].sort(lambda a, b: -cmp(a['time'], b['time']))
    else:
        info['reports'] = []
    def format_info(info):
        new_info = {}
        for key, value in info.items():
            if key == 'patches':
                new_info['patches'] = format_patches(ticket, value)
            elif key == 'reports':
                pass
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
        info = trac.scrape(ticket)
    except:
        info = tickets.find_one({'id': ticket})
    status = get_ticket_status(info, base=base)[1]
    response = make_response(open('images/%s-blob.png' % status_colors[status]).read())
    response.headers['Content-type'] = 'image/png'
    return response

@app.route("/report/<int:ticket_id>", methods=['POST'])
def post_report(ticket_id):
    try:
        ticket = db.lookup_ticket(ticket_id)
        if 'reports' not in ticket:
            ticket['reports'] = []
        report = json.loads(request.form.get('report'))
        assert isinstance(report, dict)
        for fld in ['status', 'patches', 'spkgs', 'base', 'machine', 'time']:
            assert fld in report
        ticket['reports'].append(report)
        db.logs.put(request.files.get('log'), _id=log_name(ticket_id, report))
        db.save_ticket(ticket)
        return "done"
    except:
        traceback.print_exc()
        return "bad"

def log_name(ticket_id, report):
    return "/log/%s/%s/%s" % (ticket_id, '/'.join(report['machine']), report['time'])


@app.route("/log/<path:log>")
def get_log(log):
    path = "/log/" + log
    if not logs.exists(path):
        data = "No such log!"
    else:
        data = bz2.decompress(logs.get(path).read())
    response = make_response(data)
    response.headers['Content-type'] = 'text/plain'
    return response

status_order = ['new', 'applied', 'started', 'built', 'tested']

status_colors = {
    'new': 'white',
    'started': 'red',
    'applied': 'red',
    'built': 'yellow',
    'tested': 'green',
}

@app.route("/blob/<status>")
def status_image(status):
    response = make_response(open('images/%s-blob.png' % status_colors[status]).read())
    response.headers['Content-type'] = 'image/png'
    return response

def get_ticket_status(ticket, base=None):
    all = current_reports(ticket)
    if len(all):
        index = min(status_order.index(report['status']) for report in all)
        return len(all), status_order[index]
    else:
        return 0, 'new'
    
if __name__ == '__main__':

    parser = OptionParser()
    parser.add_option("-b", "--base", dest="base")
    parser.add_option("-p", "--port", dest="port")
    (options, args) = parser.parse_args()

    base = options.base
    app.run(debug=True, host="0.0.0.0", port=int(options.port))
