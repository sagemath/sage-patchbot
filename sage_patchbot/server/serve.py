# -*- coding: utf-8 -*-
# global python imports
import os
import bz2
import json
import traceback
import re
import time
import difflib
from optparse import OptionParser
from flask import Flask, render_template, make_response, request, Response
from datetime import datetime

from io import StringIO

from urllib.parse import quote

# imports from patchbot sources
from ..trac import scrape
from ..util import (now_str, current_reports, latest_version,
                    comparable_version, date_parser)
from ..patchbot import filter_on_authors

from . import db
from .db import tickets


IMAGES_DIR = os.path.join(os.path.dirname(__file__), 'images')
# oldest version of sage about which we still care
# OLDEST = comparable_version('7.6')
# see master_branch instead

# machines that are banned from posting their reports
BLACKLIST = ['sage4', 'Gentoo Base System/2.2/x86_64/4.14.78-gentoo/sage4']


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


@timed_cached_function()
def latest_base(betas=True):
    versions = list(tickets.find({'id': 0}).distinct('reports.base'))
    if not betas:
        versions = list(filter(re.compile(r'[0-9.]+$').match, versions))
    versions.sort(key=comparable_version)

    if versions:
        return versions[-1]
    else:
        return None


app = Flask(__name__)


def get_query(args) -> dict:
    """
    Prepare the precise query for the database.

    The result is a mongo-query dict.

    Allowed keywords are:

    - status
    - authors
    - author
    - participant
    - machine
    - ticket
    - base

    get_query({'participant':'yop'})
    """
    if 'query' in args:
        # already formatted for mongo
        query = json.loads(args.get('query'))
    else:
        status = args.get('status', 'needs_review')
        if status == 'all':
            query = {}
        elif status in ('new', 'closed'):
            query = {'status': {'$regex': status + '.*'}}
        elif status in ('open',):
            query = {'status': {'$regex': 'needs_.*|positive_review'}}
        else:
            query = {'status': status}

        if 'authors' in args:
            query['authors'] = {'$in': args.get('authors')}
        elif 'author' in args:
            query['authors'] = args.get('author')

        if 'participant' in args:
            query['participants'] = args.get('participant')

        if 'machine' in args:
            machine = args.getlist('machine')
            if len(machine) == 1:
                # Old URL format where 'machine' was given in a single query
                # argument with the components separated by ':'
                machine = machine.split(':')

            query['reports.machine'] = machine

        if 'ticket' in args:
            query['id'] = int(args['ticket'])

        if 'base' in args:
            base = args.get('base')
            if base == 'latest' or base == 'develop':
                query['reports.base'] = latest_base()
            elif base != 'all':
                query['reports.base'] = base

    query['milestone'] = {'$ne': 'sage-duplicate/invalid/wontfix'}

    print(query)
    return query


@app.route("/")
@app.route("/ticket")
@app.route("/ticket/")
def ticket_list():
    authors = None
    machine = None

    if 'base' in request.args:
        base = request.args.get('base')
        if base == 'all':
            base = None
        elif base == 'develop':
            base = 'latest'
    else:
        base = 'latest'

    query = get_query(request.args)
    if 'machine' in request.args:
        machine = request.args.getlist('machine')
        if len(machine) == 1:
            # Old URL format where 'machine' was given in a single query
            # argument with the components separated by ':'
            machine = machine.split(':')
    if 'authors' in request.args:
        authors = request.args.get('authors').split(':')
    limit = int(request.args.get('limit', 1000))
    raw_mode = request.args.get('raw', False)

    projection = {'_id': False}

    if raw_mode is not False and raw_mode != 'full':
        # In order to limit the size of the response, omit git_logs and plugin
        # results from the data
        projection.update({'reports.git_log': False,
                           'reports.plugins': False})

    print('ticket query + projection: ', query, projection)

    order = ('last_trac_activity', -1)
    cursor = tickets.find(query, projection).sort(*order).limit(limit)
    all = filter_on_authors(cursor, authors)
    if raw_mode is not False:
        # raw json file for communication with patchbot clients
        def filter_reports(all):
            for ticket in all:
                current = sorted(current_reports(ticket),
                                 key=lambda report: report['time'],
                                 reverse=True)
                # Take only the 10 latest reports
                ticket['reports'] = current[:10]
                yield ticket
        all = filter_reports(all)
        if 'pretty' in request.args:
            indent = 4
        else:
            indent = None
        response = make_response(json.dumps(list(all), default=lambda x: None,
                                            indent=indent))
        response.headers['Content-type'] = 'text/plain; charset=utf-8'
        return response

    summary = {key: 0 for key in status_order}

    def preprocess(all):
        for ticket in all:
            ticket['report_count'], ticket['report_status'], ticket['report_status_composite'] = get_ticket_status(ticket, machine=machine, base=base or 'latest')
            if 'reports' in ticket:
                ticket['pending'] = len([r for r in ticket['reports']
                                         if r['status'] == 'Pending'])
            summary[ticket['report_status']] += 1
            yield ticket

    ticket0 = tickets.find_one({'id': 0})
    if ticket0 is not None and 'reports' in ticket0:
        base_status = get_ticket_status(ticket0, base)
        versions = list(set(report['base'] for report in ticket0['reports']))
        versions.sort(key=comparable_version)
        extract_masters = [v for v in versions if len(v.split('.')) == 2]
        if not extract_masters:
            v = versions[-1].split('.')
            # we have no trace of reports on the previous master (sigh)
            # this could happen after a zelous database cleaning
            master_branch = comparable_version(f"{v[0]}.{int(v[1]) - 1}")
        else:
            master_branch = comparable_version(extract_masters[-1])
        versions = [v for v in versions
                    if comparable_version(v) >= master_branch]
        versions = [(v, get_ticket_status(ticket0, v)) for v in versions]
    else:
        versions = []
        base_status = (0, 'New', 'New')

    return render_template("ticket_list.html", tickets=preprocess(all),
                           summary=summary, base=base, base_status=base_status,
                           versions=versions, status_order=status_order)


class MachineStats(object):
    def __init__(self, name):
        self.name = name
        self.fresh_tickets = set()
        self.all_tickets = set()
        self.report_count = 0
        self.last_report = ''
        self.quoted_name = [quote(piece) for piece in name]

    def add_report(self, report, ticket):
        self.report_count += 1
        self.all_tickets.add(ticket['id'])
        if report.get('git_commit') == ticket.get('git_commit'):
            self.fresh_tickets.add(ticket['id'])
        self.last_report = max(report['time'], self.last_report)

    def __lt__(self, other):
        return self.last_report < other.last_report

    def __gt__(self, other):
        return self.last_report > other.last_report

    def __le__(self, other):
        return self.last_report <= other.last_report

    def __ge__(self, other):
        return self.last_report >= other.last_report

    def __eq__(self, other):
        return self.last_report == other.last_report

    def __ne__(self, other):
        return self.last_report != other.last_report


@app.route("/machines")
def machines():
    """
    list of recently working machines, with some statistics
    """
    # aggregate requires server version >= 2.1.0
    query = get_query(request.args)
    if 'authors' in request.args:
        authors = request.args.get('authors').split(':')
    else:
        authors = None
    all = filter_on_authors(tickets.find(query).limit(100), authors)
    machines = {}
    for ticket in all:
        for report in ticket.get('reports', []):
            machine = tuple(report['machine'])
            if machine in machines:
                stats = machines[machine]
            else:
                stats = machines[machine] = MachineStats(machine)
            stats.add_report(report, ticket)
    return render_template("machines.html",
                           machines=reversed(sorted(machines.values())),
                           len=len,
                           status=request.args.get('status', 'needs_review'))


@app.route("/ticket/<int:ticket>/")
def render_ticket(ticket):
    """
    reports on a given ticket

    possible options: ?force and ?kick

    ?force will refresh the info in the patchbot-server database

    ?kick will tell the patchbot-clients to retry the ticket

    ?base to select reports according to their base
    """
    latest = latest_base()

    if 'base' in request.args:
        chosen_base = request.args.get('base')
    else:
        chosen_base = 'all' if ticket != 0 else 'develop'

    if chosen_base == 'latest' or chosen_base == 'develop':
        chosen_base = latest

    try:
        info = scrape(ticket, db=db, force='force' in request.args)
    except:
        info = tickets.find_one({'id': ticket})

    if info is None:
        return "No such ticket."
    if 'kick' in request.args:
        info['retry'] = True
        db.save_ticket(info)

    def sort_key(a):
        return a['time']
    if 'reports' in info:
        info['reports'].sort(key=sort_key, reverse=True)
    else:
        info['reports'] = []

    base_reports = base_reports_by_machine_and_base()

    old_reports = list(info['reports'])
    prune_pending(info)
    if old_reports != info['reports']:
        db.save_ticket(info)

    def format_info(info):
        new_info = {}
        for key, value in info.items():
            if key in ['patches', 'reports', 'pending']:
                pass
            elif key == 'depends_on':
                deps_status = {}

                def is_int(a):
                    try:
                        int(a)
                        return True
                    except ValueError:
                        return False
                for dep in tickets.find({'id': {'$in': [int(a) for a in value
                                                        if is_int(a)]}},
                                        ['status', 'id']):
                    if 'closed' in dep['status']:
                        dep['style'] = 'text-decoration: line-through'
                    else:
                        dep['style'] = ''
                    deps_status[dep['id']] = dep
                new_info[key] = ', '.join("<img src='/ticket/%s/status.svg?fast' height=16><a href='/ticket/%s' style='%s'>%s</a>" % (a, a, deps_status[a]['style'], a) for a in value)
            elif key == 'authors':
                new_info[key] = ', '.join("<a href='/ticket/?author=%s'>%s</a>" % (a, a) for a in value)
            elif key == 'authors_fullnames':
                link = u"<a href='https://git.sagemath.org/sage.git/log/?qt=author&amp;q={}'>{}</a>"
                auths = u", ".join(link.format(a.replace(u" ", u"%20"), a)
                                   for a in value)
                new_info[key] = auths
            elif key == 'participants':
                parts = ', '.join("<a href='/ticket/?participant=%s'>%s</a>" % (a, a) for a in value)
                new_info[key] = parts
            elif key == 'git_branch':
                new_info[key] = '<a href="https://git.sagemath.org/sage.git/log/?h=%s">%s</a>' % (value, value)
            elif key == 'component':
                new_info[key] = '<a href="https://trac.sagemath.org/query?status=!closed&component=%s">%s</a>' % (value, value)
            elif key == 'spkgs':
                pass
            elif isinstance(value, list):
                new_info[key] = ', '.join(value)
            elif key not in ('id', '_id'):
                new_info[key] = value
        return new_info

    def format_git_describe(res):
        if res:
            if '-' in res:
                tag, commits = res.split('-')[:2]
                return "%s + %s commits" % (tag, commits)
            elif 'commits' in res:
                # old style
                return res
            else:
                return res + " + 0 commits"
        else:
            return '?'

    def preprocess_reports(all):
        for item in all:
            base_of_this_report = item['base']
            base_report = base_reports.get(item['base'] + "/" + "/".join(item['machine']), base_reports.get(item['base']))
            if base_report:
                item['base_log'] = quote(log_name(0, base_report))
            if 'git_base' in item:
                git_log = item.get('git_log')
                item['git_log_len'] = '?' if git_log is None else len(git_log)
            item['raw_base'] = item['base']
            if comparable_version(item['base']) <= comparable_version(latest):
                item['base'] = "<span style='color: red'>%s</span>" % item['base']
            if 'time' in item:
                item['log'] = log_name(info['id'], item)
            if 'git_commit_human' not in item:
                item['git_commit_human'] = "%s new commits" % len(item['log'])
            for x in ('commit', 'base', 'merge'):
                field = 'git_%s_human' % x
                item[field] = format_git_describe(item.get(field, None))
            item['machine'] = band_aid_for_machine(item['machine'])
            if chosen_base == 'all' or chosen_base == base_of_this_report:
                yield item

    def band_aid_for_machine(mach):
        return [quote(txt) for txt in mach]

    def normalize_plugin(plugin):
        while len(plugin) < 3:
            plugin.append(None)
        return plugin

    def sort_fields(items):
        return sorted(items, key=(lambda x: (x[0] != 'title', x)))

    status_data = get_ticket_status(info, base=latest)[1]  # single status

    return render_template("ticket.html",
                           reports=preprocess_reports(info['reports']),
                           ticket=ticket, info=format_info(info),
                           status=status_data,
                           normalize_plugin=normalize_plugin,
                           sort_fields=sort_fields)


@timed_cached_function(10)
def base_reports_by_machine_and_base():
    """
    reports on the base branch (pseudo-ticket 0)
    """
    return reports_by_machine_and_base(tickets.find_one({'id': 0}))


def reports_by_machine_and_base(ticket):
    """
    reports on the given ticket
    """
    all = {}

    def sort_key(a):
        return a['time']

    if 'reports' in ticket:
        # oldest to newest
        for report in sorted(ticket['reports'], key=sort_key):
            all[report['base']] = report
            all[report['base'] + "/" + "/".join(report['machine'])] = report
    return all


def is_good_machine(machine):
    """
    Check that this machine has a recent report on ticket 0 (base)
    with result being ``TestsPassed`` or ``TestsFailed``.
    """
    ticket = tickets.find_one({'id': 0})

    def sort_key(a):
        return a['time']
    if 'reports' not in ticket:
        return True  # emergency case, when base reports were deleted
    # oldest to newest
    reports = sorted(ticket['reports'], key=sort_key)
    # just use the short machine name
    reports = [rep for rep in reports if rep['machine'][-1] == machine]
    if not reports:
        return False
    rep = reports[-1]
    return rep['status'] in ['TestsPassed', 'TestsFailed']


# The fact that this image is in the trac template lets the patchbot
# know when a page gets updated.


@app.route("/ticket/<int:ticket>/base.svg")
def render_ticket_base_svg(ticket):
    """
    Return the svg base version image for the given ticket.
    """
    try:
        if 'fast' in request.args:
            info = tickets.find_one({'id': ticket})
        else:
            info = scrape(ticket, db=db)
    except:
        info = tickets.find_one({'id': ticket})

    if 'base' in request.args:
        base = request.args.get('base')
    else:
        base = latest_version(info.get('reports', []))

    if base is None:
        base = ''

    base = base.replace("alpha", u'α').replace("beta", u'β')
    split_base = base.split('.')
    if len(split_base) == 2:
        v_main = base
        v_sub = ''
        baseline = 225
    elif len(split_base) == 3:
        x, y, z = split_base
        v_main = x + '.' + y
        v_sub = z
        baseline = 150
    else:
        v_main = ''
        v_sub = ''
        baseline = 150
    svg = render_template('icon-Version.svg', version_main=v_main,
                          version_sub=v_sub, version_baseline=baseline)
    response = make_response(svg)
    response.content_type = 'image/svg+xml'
    return response


@app.route("/ticket/<int:ticket>/status.svg")
def render_ticket_status_svg(ticket):
    """
    Return the svg status image for the given ticket.

    This displays the current status (TestsPassed, etc) as an svg icon.
    """
    try:
        if 'fast' in request.args:
            info = tickets.find_one({'id': ticket})
        else:
            info = scrape(ticket, db=db)
    except:
        info = tickets.find_one({'id': ticket})

    if 'base' in request.args:
        base = request.args.get('base')
    else:
        base = latest_version(info.get('reports', []))

    status = get_ticket_status(info, base=base)[1]  # single status
    path = status_image_path(status, image_type='svg')

    # with no base
    response = make_response(open(path).read())
    response.headers['Content-type'] = 'image/svg+xml'
    response.headers['Cache-Control'] = 'no-cache'
    return response


@app.route("/report/<int:ticket_id>", methods=['POST'])
def post_report(ticket_id):
    """
    Posting a report to the database of reports.

    The machine need to have a correct report on ticket 0,
    see :func:`is_good_machine`
    """
    try:
        ticket = tickets.find_one({'id': ticket_id})
        if ticket is None:
            ticket = scrape(ticket_id, db=db)
        if 'reports' not in ticket:
            ticket['reports'] = []
        report = json.loads(request.form.get('report'))
        assert (isinstance(report, dict)), "report is not a dict"
        for fld in ['status', 'spkgs', 'base', 'machine', 'time']:
            assert (fld in report), "{} missing in report".format(fld)

        # handling misbehaviour of patchbot clients
        machine_name = report['machine'][-1]
        if machine_name in BLACKLIST:
            msg = 'machine {} is blacklisted'.format(machine_name)
            raise RuntimeError(msg)
        if ticket_id != 0 and not is_good_machine(machine_name):
            msg = 'machine {} fails on ticket 0'.format(machine_name)
            raise RuntimeError(msg)

        prune_pending(ticket, report['machine'])
        ticket['reports'].append(report)
        log_filename = log_name(ticket_id, report)
        db.logs.put(request.files.get('log'),
                    _id=log_filename,
                    filename=log_filename)
        if 'retry' in ticket:
            ticket['retry'] = False
        ticket['last_activity'] = now_str()
        db.save_ticket(ticket)
        return "ok (report successfully posted)"
    except:
        traceback.print_exc()
        return "error in posting the report"


def log_name(ticket_id, report):
    machine = [txt.replace('#', '%23') for txt in report['machine']]
    return "/log%s/%s/%s/%s" % (
        '/Pending' if report['status'] == 'Pending' else '',
        ticket_id,
        '/'.join(machine),
        report['time'])


def prune_pending(ticket, machine=None, timeout=None):
    """
    Remove pending reports from ``ticket.reports``,
    as well as the corresponding log.

    A pending report is removed if ``machine`` is matched
    or ``report.time`` is longer than ``timeout`` old.

    The ``timeout`` is currently set to 6 hours by default

    The difference with the ``prune_pending`` appearing in util.py
    it that this one also removes the corresponding log in the
    database (which does not exist on a patchbot client).
    """
    if timeout is None:
        timeout = 6 * 60 * 60
    if 'reports' in ticket:
        reports = ticket['reports']
    else:
        return []
    now = datetime.utcnow()  # in the utc timezone
    for report in list(reports):
        if report['status'] == 'Pending':
            t = date_parser(report['time'])
            if report['machine'] == machine:
                reports.remove(report)
                db.remove_log(log_name(ticket, report))
            elif (now - t).total_seconds() > timeout:
                reports.remove(report)
                db.remove_log(log_name(ticket, report))
    return reports


def shorten(lines):
    """
    Extract a shorter log from the full log by removing boring parts
    """
    timing = re.compile(r'\s*\[(\d+ tests?, )?\d+\.\d* s\]\s*$')
    skip = re.compile(r'sage -t.*\(skipping\)|'
                      r'changing mode|'
                      r'[Ww]riting|'
                      r'[Aa]dding|'
                      r'[Bb]yte-compiling|'
                      r'[Ee]nabling|'
                      r'[Cc]opying|'
                      r'\S+: \d+% \(\d+ of \d+\)|'
                      r'Build finished|'
                      r'\[dochtml\]|'
                      r'\[sagelib-.*\]|'
                      r'mkdir|'
                      r'[Cc]reating|'
                      r'Deleting empty directory|'
                      r';;;.*$')
    gcc = re.compile(r'gcc|g\+\+')
    prev = None
    in_plugin = False
    from ..patchbot import boundary
    plugin_start = re.compile(boundary('.*', 'plugin'))
    plugin_end = re.compile(boundary('.*', 'plugin_end'))

    for line in StringIO(lines):
        if line.startswith('='):
            if plugin_end.match(line):
                if prev:
                    yield prev
                    prev = None
                in_plugin = False
            elif plugin_start.match(line):
                if prev:
                    yield prev
                    prev = None
                yield line
                in_plugin = True
        if in_plugin:
            prev = line
            continue

        if skip.search(line):
            pass
        elif prev is None:
            prev = line
        elif prev.startswith('sage -t') and timing.match(line):
            prev = None
        elif prev.startswith('python `which cython`') and '-->' in line:
            prev = None
        elif gcc.match(prev) and (gcc.match(line) or
                                  line.startswith('Time to execute')):
            prev = line
        else:
            if prev is not None:
                yield prev
            prev = line

    if prev is not None:
        yield prev


def extract_plugin_log(data, plugin):
    """
    Extract from data the log of a given plugin.
    """
    from ..patchbot import boundary
    start = boundary(plugin, 'plugin') + "\n"
    end = boundary(plugin, 'plugin_end') + "\n"
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
    if not db.logs.exists(path):
        data = "No such log !\n{}".format(path)
    else:
        data = bz2.decompress(db.logs.get(path).read()).decode()
    if 'plugin' in request.args:
        plugin = request.args.get('plugin')
        data = extract_plugin_log(data, plugin)
        if 'diff' in request.args:
            header = data[:data.find('\n')]
            base = request.args.get('base')
            ticket_id = request.args.get('ticket')
            base_data = bz2.decompress(db.logs.get(request.args.get('diff')).read())
            base_data = base_data.decode()
            base_data = extract_plugin_log(base_data, plugin)
            diff = difflib.unified_diff(base_data.split('\n'), data.split('\n'), base, "%s + #%s" % (base, ticket_id), n=0)
            data = '\n'.join(('' if item[0] == '@' else item)
                             for item in diff)
            if not data:
                data = "No change."
            data = header + "\n\n" + data
        return render_template("plugin_template.html",
                               plugin_name=plugin, plugin_text=data)

    if 'short' in request.args:
        response = Response(shorten(data))
    else:
        response = make_response(data)
    response.headers['Content-type'] = 'text/plain; charset=utf-8'
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
                    response = make_response(json.dumps(plugin[2],
                                                        default=lambda x: None,
                                                        indent=4))
                    response.headers['Content-type'] = 'text/plain; charset=utf-8'
                    return response
            return "Unknown plugin: " + plugin_name
    return "Unknown report: " + timestamp


status_order = ['New', 'ApplyFailed', 'BuildFailed', 'TestsFailed',
                'PluginFailed', 'TestsPassed', 'TestsPassedOnRetry', 'Pending',
                'PluginOnlyFailed', 'PluginOnly', 'NoPatch', 'Spkg']


@app.route('/icon-Version.svg')
def create_base_image_svg():
    """
    Create an svg picture displaying a version number.

    EXPERIMENTAL !
    """
    base = request.args.get('base', '7.2.beta8')
    base = base.replace("alpha", u'α').replace("beta", u'β')
    split_base = base.split('.')
    if len(split_base) == 2:
        v_main = base
        v_sub = ''
    else:
        x, y, z = split_base
        v_main = x + '.' + y
        v_sub = z
    svg = render_template('icon-Version.svg', version_main=v_main,
                          version_sub=v_sub)
    response = make_response(svg)
    response.content_type = 'image/svg+xml'
    return response


@app.route("/svg/<status>")
def status_image_svg(status):
    """
    Return the blob image (as a web page) for a single status

    This is for the 'svg' icon set.

    For example, see https://patchbot.sagemath.org/blob/BuildFailed

    or https://patchbot.sagemath.org/blob_svg/TestsPassed
    """
    liste = status.split(',')
    # Only one possible status displayed. Which one to choose ?
    # stupid choice for the moment
    if len(liste) > 1:
        status = liste[0]
    path = status_image_path(status, image_type='svg')
    response = make_response(open(path).read())
    response.headers['Content-type'] = 'image/svg+xml'
    response.headers['Cache-Control'] = 'max-age=3600'
    return response


def status_image_path(status, image_type='png'):
    """
    Return the blob image address for a single status.

    There are two different icon sets : 'png' and 'svg'

    For example, the result for 'TestsPassed' should be
    images/icon-TestsPassed.png
    """
    if image_type == 'png':
        return os.path.join(IMAGES_DIR, 'icon-{}.png'.format(status))
    else:
        return os.path.join(IMAGES_DIR, 'icon-{}.svg'.format(status))


def create_status_image(status, base=None):
    """
    Return a composite blob image for a concatenation of status

    This is for the 'png' icon set.

    INPUT:

    - status -- a single or composite status as a single string

    - base -- the base

    EXAMPLES::

        create_status_image('TestsPassed,TestsFailed')
        create_status_image('NoPatch')
    """
    if ',' in status:
        status_list = status.split(',')
        # Ignore plugin only...
        while 'PluginOnly' in status_list and len(status_list) > 1:
            status_list.remove('PluginOnly')
        # If tests passed but a plugin-only failed, report as if the
        # plugin failed.
        if 'TestsPassed' in status_list:
            for ix, status in enumerate(status_list):
                if status_list[ix] == 'PluginOnlyFailed':
                    status_list[ix] = 'PluginFailed'
        if not status_list:
            path = status_image_path('New')
        elif len(set(status_list)) == 1:
            status = status_list[0]
            path = status_image_path(status)
        else:
            try:
                from PIL import Image
                import numpy
                cache_dir = os.path.join(IMAGES_DIR, '_cache')
                if not os.path.exists(cache_dir):
                    os.mkdir(cache_dir)

                filename = ','.join(status_list) + 'blob.png'
                path = os.path.join(cache_dir, filename)
                if not os.path.exists(path):
                    composite = numpy.asarray(Image.open(status_image_path(status_list[0]))).copy()
                    height, width, _ = composite.shape
                    for ix, status in enumerate(reversed(status_list)):
                        slice = numpy.asarray(Image.open(status_image_path(status)))
                        start = ix * width / len(status_list)
                        end = (ix + 1) * width / len(status_list)
                        composite[:, start:end, :] = slice[:, start:end, :]
                    Image.fromarray(composite, 'RGBA').save(path)
            except ImportError as exn:
                print(exn)
                status = min_status(status_list)
                path = status_image_path(status)
    else:
        path = status_image_path(status)
    if base is not None:
        try:
            from PIL import Image, ImageDraw
            im = Image.open(path)
            ImageDraw.Draw(im).text((5, 20), base.replace("alpha", "a").replace("beta", "b"), fill='#FFFFFF')
            output = StringIO()
            im.save(output, format='png')
            return output.getvalue()
        except ImportError:
            return open(path).read()
    else:
        return open(path).read()


def min_status(status_list):
    """
    Return the minimal status among a list of status.

    The order is deduced from a total order encoded in ``status_order``.

    EXAMPLES::

        >>> min_status(['TestsPassed', 'TestsFailed'])
    """
    index = min(status_order.index(status) for status in status_list)
    return status_order[index]


@app.route("/robots.txt")
def robots():
    """
    Return a robot instruction web page

    See https://patchbot.sagemath.org/robots.txt for the result.

    EXAMPLES::

        sage: from serve import robots
        sage: robots()
        ?
    """
    return render_template("robots.txt")


@app.route("/favicon.ico")
def favicon():
    """
    Return the favicon image as a web page.

    This is currently a 16 x 16 png version of icon-TestsPassed.svg.

    See https://patchbot.sagemath.org/favicon.ico for the result.

    EXAMPLES::

        sage: from serve import favicon
        sage: favicon()
    """
    response = make_response(open(os.path.join(IMAGES_DIR, 'favicon.png')).read())
    response.headers['Content-type'] = 'image/png'
    return response


def get_ticket_status(ticket, base=None, machine=None):
    """
    Return the status of the ticket in the database.

    INPUT:

    - ``ticket`` -- dictionary

    - ``base`` -- keyword passed to ``current_reports``

    - ``machine`` -- if given, only look at this machine's reports

    OUTPUT:

    a triple (number of reports, single status, composite status)

    Note that ``Spkg``, ``NoPatch`` and ``New`` are not got from any report.
    """
    all = current_reports(ticket, base=base)
    if machine is not None:
        all = [r for r in all if r['machine'] == machine]
    if all:
        status_list = [report['status'] for report in all]
        if len(set(status_list)) == 1:
            composite = single = status_list[0]
        else:
            composite = ','.join(status_list)
            single = min_status(status_list)
        return len(all), single, composite
    elif ticket['spkgs']:
        return 0, 'Spkg', 'Spkg'
    elif not ticket.get('git_commit'):
        return 0, 'NoPatch', 'NoPatch'
    else:
        return 0, 'New', 'New'


def main(args):
    parser = OptionParser()
    parser.add_option("-p", "--port", dest="port")
    parser.add_option("--debug", dest="debug", action='store_true')
    (options, args) = parser.parse_args(args)

    app.run(debug=options.debug, host="0.0.0.0", port=int(options.port))
