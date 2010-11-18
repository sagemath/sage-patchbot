import sys
from flask import Flask, render_template, make_response
import pymongo
import trac

from db import tickets, reports

app = Flask(__name__)

@app.route("/")
def main():
    all = tickets.find({'status': 'needs_review'}).sort('id')
    def preprocess(all):
        for ticket in all:
            ticket['report_count'], ticket['report_status'] = get_ticket_status(ticket, base)
            yield ticket
    return render_template("ticket_list.html", tickets=preprocess(all))

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

@app.route("/ticket/<int:ticket>")
@app.route("/ticket/<int:ticket>/")
def render_ticket(ticket):
    try:
        info = trac.scrape(ticket)
    except:
        info = tickets.find_one({'id': ticket})
    all = reports.find({'ticket': ticket}).sort([('time', pymongo.DESCENDING)])
    def format_info(info):
        new_info = {}
        for key, value in info.items():
            if key == 'patches':
                new_info['patches'] = format_patches(ticket, value)
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
            yield item
    return render_template("ticket.html", reports=preprocess_reports(all), ticket=ticket, info=format_info(info), status=get_ticket_status(info, base=base)[1])

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
    query = {
        'ticket': ticket['id'],
        'patches': ticket['patches'],
        'spkgs': ticket['spkgs'],
    }
    if base:
        query['base'] = base
    all = list(reports.find(query))
    if len(all):
        index = min(status_order.index(report['status']) for report in all)
        return len(all), status_order[index]
    else:
        return 0, 'new'
    

base = sys.argv[1]
app.run(debug=True, host="0.0.0.0", port=21100)
