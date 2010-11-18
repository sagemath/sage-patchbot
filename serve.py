import sys
from flask import Flask, render_template, make_response
import trac

from db import tickets, reports

app = Flask(__name__)

@app.route("/")
def main():
    all = tickets.find({'status': 'needs_review'}).sort('id')
    return render_template("ticket_list.html", tickets=all)

def format_patches(ticket, patches):
    return ("<ol><li>" 
        + "\n<li>".join("<a href='%s'>%s</a>" % (trac.get_patch_url(ticket, patch, raw=False), patch) for patch in patches) 
        + "</ol>")

@app.route("/ticket/<int:ticket>")
@app.route("/ticket/<int:ticket>/")
def ticket_status(ticket):
    info = tickets.find_one({'id': ticket})
    all = reports.find({'ticket': ticket})
    def format_info(info):
        for key, value in info.items():
            if key == 'patches':
                info['patches'] = format_patches(ticket, value)
            elif isinstance(value, list):
                info[key] = ', '.join(value)
        del info['_id']
        del info['id']
        return info
    return render_template("ticket.html", reports=all, ticket=ticket, info=format_info(info))

status_colors = {
    'new': 'white',
    'started': 'red',
    'built': 'yellow',
    'tested': 'green',
}

@app.route("/blob/<status>")
def status_image(status):
    response = make_response(open('images/%s-blob.png' % status_colors[status]).read())
    response.headers['Content-type'] = 'image/png'
    return response

app.run(debug=True, host="0.0.0.0", port=21100)
