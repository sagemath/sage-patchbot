import os, sys, bz2, json, traceback, re, collections, urllib, time
import difflib
from cStringIO import StringIO
from optparse import OptionParser
from flask import Flask, render_template, make_response, request, Response
import pymongo
import trac
import patchbot
import db

from db import tickets, logs
from util import now_str, current_reports, comparable_version, latest_version

def timed_cached_function(refresh_rate=60):
    def decorator(func):
        cache = {}
        def wrap(*args):
            now = time.time()
            if args in cache:
                latest_update, res = cache[args]
                if now - latest_update < refresh_rate:
                    return res
            res = func(*args)
            cache[args] = now, res
            return res
        return wrap
    return decorator

app = Flask(__name__)

@app.route("/reports")
def reports():
    pass
    
@app.route("/trusted")
@app.route("/trusted/")
def trusted_authors():
    authors = collections.defaultdict(int)
    for ticket in tickets.find({'status': 'closed : fixed'}):
        for author in ticket["authors"]:
            authors[author] += 1
    if 'pretty' in request.args:
        indent = 4
    else:
        indent = None
    response = make_response(json.dumps(authors, default=lambda x: None, indent=indent))
    response.headers['Content-type'] = 'text/plain'
    return response

@app.route("/")
@app.route("/ticket")
@app.route("/ticket/")
def ticket_list():
    authors = None
    machine = None
    if 'query' in request.args:
        query = json.loads(request.args.get('query'))
    else:
        status = request.args.get('status', 'needs_review')
        if status == 'all':
            query = {}
        elif status in ('new', 'closed'):
            query = {'status': {'$regex': status + '.*' }}
        elif status in ('open'):
            query = {'status': {'$regex': 'needs_.*|positive_review' }}
        else:
            query = {'status': status}
        if 'todo' in request.args:
            query['patches'] = {'$not': {'$size': 0}}
            query['spkgs'] = {'$size': 0}
        if 'authors' in request.args:
            authors = request.args.get('authors').split(':')
            query['authors'] = {'$in': authors}
        if 'machine' in request.args:
            machine = request.args.get('machine').split('/')
            query['reports.machine'] = machine
    if 'order' in request.args:
        order = request.args.get('order')
    else:
        order = 'last_activity'
    if 'base' in request.args:
        base = request.args.get('base')
        if base == 'all':
            base = None
    else:
        base = 'latest'
    if 'author' in request.args:
        query['authors'] = request.args.get('author')
    if 'participant' in request.args:
        query['participants'] = request.args.get('participant')
    all = patchbot.filter_on_authors(tickets.find(query).sort(order), authors)
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
            ticket['report_count'], ticket['report_status'], ticket['report_status_composite'] = get_ticket_status(ticket, machine=machine, base=base or 'latest')
            if 'reports' in ticket:
                ticket['pending'] = len([r for r in ticket['reports'] if r['status'] == 'Pending'])
            summary[ticket['report_status']] += 1
            yield ticket
    ticket0 = db.lookup_ticket(0)
    versions = list(set(report['base'] for report in ticket0['reports']))
    versions.sort(trac.compare_version)
    versions = [(v, get_ticket_status(ticket0, v)) for v in versions if v != '4.7.']
    return render_template("ticket_list.html", tickets=preprocess(all), summary=summary, base=base, base_status=get_ticket_status(db.lookup_ticket(0), base), versions=versions, status_order=status_order)

def format_patches(ticket, patches, deps=None, required=None):
    if deps is None:
        deps = []
    if required is not None:
        required = set(required)
    def format_item(item):
        if required is None or item in required:
            note = ""
        else:
            note = "<span style='color: red'>(mismatch)</span>"
        item = str(item)
        if '#' in item:
            url = trac.get_patch_url(ticket, item, raw=False)
            title = item
        elif '.' in item:
            url = '/?base=%s' % item
            title = 'sage-%s' % item
        else:
            url = '/ticket/%s' % item
            title = '#%s' % item            
        return "<a href='%s'>%s</a> %s" % (url, title, note)
        
    missing_deps = missing_patches = ''
    if required is not None:
        required_patches_count = len([p for p in required if '#' in str(p)])
        if len(deps) < len(required) - required_patches_count:
            missing_deps = "<li><span style='color: red'>(missing deps)</span>\n"
        if len(patches) < required_patches_count:
            missing_patches = "<li><span style='color: red'>(missing patches)</span>\n"
    return ("<ol>"
        + missing_deps
        + "<li>\n"
        + "\n<li>".join(format_item(patch) for patch in (deps + patches)) 
        + missing_patches
        + "</ol>")

@app.route("/ticket/<int:ticket>/")
def render_ticket(ticket):
    try:
        info = trac.scrape(ticket, db=db, force='force' in request.args)
    except:
        info = tickets.find_one({'id': ticket})
    if info is None:
        return "No such ticket."
    if 'kick' in request.args:
        info['retry'] = True
        db.save_ticket(info)
    if 'reports' in info:
        info['reports'].sort(lambda a, b: -cmp(a['time'], b['time']))
    else:
        info['reports'] = []
    
    base_reports = base_reports_by_machine_and_base()
        
    old_reports = list(info['reports'])
    patchbot.prune_pending(info)
    if old_reports != info['reports']:
        db.save_ticket(info)

    def format_info(info):
        new_info = {}
        for key, value in info.items():
            if key == 'patches':
                new_info['patches'] = format_patches(ticket, value)
            elif key == 'reports' or key == 'pending':
                pass
            elif key == 'depends_on':
                deps_status = {}
                for dep in tickets.find({'id': {'$in': [int(a) for a in value]}}, ['status', 'id']):
                    if 'closed' in dep['status']:
                        dep['style'] = 'text-decoration: line-through'
                    else:
                        dep['style'] = ''
                    deps_status[dep['id']] = dep
                new_info[key] = ', '.join("<img src='/ticket/%s/status.png?fast' height=16><a href='/ticket/%s' style='%s'>%s</a>" % (a, a, deps_status[a]['style'], a) for a in value)
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
            base_report = base_reports.get(item['base'] + "/" + "/".join(item['machine']), base_reports.get(item['base']))
            if base_report:
                item['base_log'] = urllib.quote(log_name(0, base_report))
            if 'patches' in item:
                required = info['depends_on'] + info['patches']
                item['patch_list'] = format_patches(ticket, item['patches'], item.get('deps'), required)
            if 'git_base' in item:
                git_log = item.get('git_log')
                item['git_log_len'] = '?' if git_log is None else len(git_log)
            item['raw_base'] = item['base']
            if item['base'] != base:
                item['base'] = "<span style='color: red'>%s</span>" % item['base']
            if 'time' in item:
                item['log'] = log_name(info['id'], item)
            yield item
    def normalize_plugin(plugin):
        while len(plugin) < 3:
            plugin.append(None)
        return plugin
    return render_template("ticket.html", reports=preprocess_reports(info['reports']), ticket=ticket, info=format_info(info), status=get_ticket_status(info, base=base)[2], normalize_plugin=normalize_plugin)

@timed_cached_function(10)
def base_reports_by_machine_and_base():
    return reports_by_machine_and_base(tickets.find_one({'id': 0}))

def reports_by_machine_and_base(ticket):
    all = {}
    if 'reports' in ticket:
        # oldest to newest
        for report in sorted(ticket['reports'], lambda a, b: cmp(a['time'], b['time'])):
            all[report['base']] = report
            all[report['base'] + "/" + "/".join(report['machine'])] = report
    return all

# The fact that this image is in the trac template lets the patchbot know
# when a page gets updated.
@app.route("/ticket/<int:ticket>/status.png")
def render_ticket_status(ticket):
    try:
        if 'fast' in request.args:
            info = tickets.find_one({'id': ticket})
        else:
            info = trac.scrape(ticket, db=db)
    except:
        info = tickets.find_one({'id': ticket})
    if 'base' in request.args:
        base = request.args.get('base')
    elif 'reports' in info:
        base = latest_version(current_reports(info))
    else:
        base = None
    status = get_ticket_status(info, base=base)[2]
    if 'fast' in request.args:
        display_base = None
    else:
        display_base = base
    response = make_response(create_status_image(status, base=display_base))
    response.headers['Content-type'] = 'image/png'
    response.headers['Cache-Control'] = 'no-cache'
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
        patchbot.prune_pending(ticket, report['machine'])
        ticket['reports'].append(report)
        if report['status'] != 'Pending':
            db.logs.put(request.files.get('log'), _id=log_name(ticket_id, report))
        if 'retry' in ticket:
            ticket['retry'] = False
        ticket['last_activity'] = now_str()
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

def extract_plugin_log(data, plugin):
    from patchbot import plugin_boundary
    start = plugin_boundary(plugin) + "\n"
    end = plugin_boundary(plugin, end=True) + "\n"
    all = []
    include = False
    for line in StringIO(data):
        if line == start:
            include = True
        if include:
            all.append(line)
        if line == end:
            break
    return ''.join(all)

@app.route("/ticket/<id>/log/<path:log>")
def get_ticket_log(id, log):
    return get_log(log)

@app.route("/log/<path:log>")
def get_log(log):
    path = "/log/" + log
    if not logs.exists(path):
        data = "No such log!"
    else:
        data = bz2.decompress(logs.get(path).read())
    if 'plugin' in request.args:
        plugin = request.args.get('plugin')
        data = extract_plugin_log(data, plugin)
        if 'diff' in request.args:
            header = data[:data.find('\n')]
            base = request.args.get('base')
            ticket_id = request.args.get('ticket')
            base_data = bz2.decompress(logs.get(request.args.get('diff')).read())
            base_data = extract_plugin_log(base_data, plugin)
            diff = difflib.unified_diff(base_data.split('\n'), data.split('\n'), base, "%s + #%s" % (base, ticket_id), n=0)
            data = data = '\n'.join(('' if item[0] == '@' else item) for item in diff)
            if not data:
                data = "No change."
            data = header + "\n\n" + data
            
    if 'short' in request.args:
        response = Response(shorten(data), direct_passthrough=True)
    else:
        response = make_response(data)
    response.headers['Content-type'] = 'text/plain'
    return response

@app.route("/ticket/<id>/plugin/<plugin_name>/<timestamp>/")
def get_plugin_data(id, plugin_name, timestamp):
    ticket = tickets.find_one({'id': int(id)})
    if ticket is None:
        return "Unknown ticket: " + id
    for report in ticket['reports']:
        if report['time'] == timestamp:
            for plugin in report['plugins']:
                if plugin[0] == plugin_name:
                    response = make_response(json.dumps(plugin[2], default=lambda x: None, indent=4))
                    response.headers['Content-type'] = 'text/plain'
                    return response
            return "Unknown plugin: " + plugin_name
    return "Unknown report: " + timestamp

status_order = ['New', 'ApplyFailed', 'BuildFailed', 'TestsFailed', 'PluginFailed', 'TestsPassed', 'Pending', 'PluginOnlyFailed', 'PluginOnly', 'NoPatch', 'Spkg']
# TODO: cleanup old records
# status_order += ['started', 'applied', 'built', 'tested']

status_colors = {
    'New'        : 'white',
    'ApplyFailed': 'red',
    'BuildFailed': 'orange',
    'TestsFailed': 'yellow',
    'TestsPassed': 'green',
    'PluginFailed': 'blue',
    'Pending'    : 'white',
    'PluginOnly' : 'lightgreen',
    'PluginOnlyFailed' : 'lightblue',
    'NoPatch'    : 'purple',
    'Spkg'       : 'purple',
}

@app.route("/blob/<status>")
def status_image(status):
    response = make_response(create_status_image(status))
    response.headers['Content-type'] = 'image/png'
    response.headers['Cache-Control'] = 'max-age=3600'
    return response

def create_status_image(status, base=None):
    if ',' in status:
        status_list = status.split(',')
        # Ignore plugin only...
        while 'PluginOnly' in status_list and len(status_list) > 1:
            status_list.remove('PluginOnly')
        # If tests passed but a plugin-only failed, report as if the plugin failed.
        if 'TestsPassed' in status_list:
            for ix, status in enumerate(status_list):
                if status_list[ix] == 'PluginOnlyFailed':
                    status_list[ix] = 'PluginFailed'
        if len(set(status_list)) == 1:
            status = status_list[0]
            path = status_image(status)
        else:
            try:
                from PIL import Image
                import numpy
                path = 'images/_cache/' + ','.join(status_list) + '-blob.png'
                if not os.path.exists(path):
                    composite = numpy.asarray(Image.open(status_image(status_list[0]))).copy()
                    height, width, _ = composite.shape
                    for ix, status in enumerate(reversed(status_list)):
                        slice = numpy.asarray(Image.open(status_image(status)))
                        start = ix * width / len(status_list)
                        end = (ix + 1) * width / len(status_list)
                        composite[:,start:end,:] = slice[:,start:end,:]
                    if not os.path.exists('images/_cache'):
                        os.mkdir('images/_cache')
                    Image.fromarray(composite, 'RGBA').save(path)
            except ImportError, exn:
                print exn
                status = min_status(status_list)
                path = status_image(status)
    else:
        path = status_image(status)
    if base is not None:
        try:
            from PIL import Image
            import ImageDraw
            im = Image.open(path)
            ImageDraw.Draw(im).text((5, 20), base.replace("alpha", "a").replace("beta", "b"), fill='#FFFFFF')
            output = StringIO()
            im.save(output, format='png')
            return output.getvalue()
        except ImportError:
            return open(path).read()
    else:
        return open(path).read()

def status_image(status):
    return 'images/%s-blob.png' % status_colors[status]

def min_status(status_list):
    index = min(status_order.index(status) for status in status_list)
    return status_order[index]

@app.route("/robots.txt")
def robots():
    return render_template("robots.txt")

@app.route("/favicon.ico")
def favicon():
    response = make_response(open('images/%s-blob.png' % status_colors['TestsPassed']).read())
    response.headers['Content-type'] = 'image/png'
    return response

def get_ticket_status(ticket, base=None, machine=None):
    all = current_reports(ticket, base=base)
    if machine is not None:
        all = [r for r in all if r['machine'] == machine]
    if len(all):
        status_list = [report['status'] for report in all]
        if len(set(status_list)) == 1:
            composite = single = status_list[0]
        else:
            composite = ','.join(status_list)
            single = min_status(status_list)
        return len(all), single, composite
    elif ticket['spkgs']:
        return 0, 'Spkg', 'Spkg'
    elif not ticket['patches']:
        return 0, 'NoPatch', 'NoPatch'
    else:
        return 0, 'New', 'New'

def main(args):
    parser = OptionParser()
    parser.add_option("-b", "--base", dest="base")
    parser.add_option("-p", "--port", dest="port")
    parser.add_option("--debug", dest="debug", default=False)
    (options, args) = parser.parse_args(args)

    global global_base, base
    global_base = base = options.base
    app.run(debug=options.debug, host="0.0.0.0", port=int(options.port))

if __name__ == '__main__':
    main(sys.argv)
