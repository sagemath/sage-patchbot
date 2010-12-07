TRAC_URL = "http://trac.sagemath.org/sage_trac"

import re, hashlib, urllib2, os, sys, traceback, time


def digest(s):
    """
    Computes a cryptographic hash of the string s.
    """
    return hashlib.md5(s).hexdigest()

def get_url(url):
    """
    Returns the contents of url as a string.
    """
    handle = urllib2.urlopen(url)
    data = handle.read()
    handle.close()
    return data

def get_patch_url(ticket, patch, raw=True):
    if raw:
        return "%s/raw-attachment/ticket/%s/%s" % (TRAC_URL, ticket, patch)
    else:
        return "%s/attachment/ticket/%s/%s" % (TRAC_URL, ticket, patch)

def get_patch(ticket, patch):
    return get_url(get_patch_url(ticket, patch))

def scrape(ticket_id, force=False, db=None):
    """
    Scrapes the trac page for ticket_id, updating the database if needed.
    """
    ticket_id = int(ticket_id)
    if db is not None:
        # TODO: perhaps the db caching should be extracted outside of this function...
        db_info = db.lookup_ticket(ticket_id)
        rss = get_url("%s/ticket/%s?format=rss" % (TRAC_URL, ticket_id))
        page_hash = digest(rss) # rss isn't as brittle
        if not force and db_info is not None and db_info['page_hash'] == page_hash:
            return db_info
    # TODO: Is there a better format that still has all the information?
    html = get_url("%s/ticket/%s" % (TRAC_URL, ticket_id))
    authors = set()
    patches = []
    for patch, who in extract_patches(rss):
        authors.add(who)
        patches.append(patch + "#" + digest(get_patch(ticket_id, patch)))
    authors = list(authors)
    data = {
        'id'            : ticket_id,
        'title'         : extract_title(rss),
        'page_hash'     : page_hash,
        'status'        : extract_status(html),
        'priority'      : extract_priority(html),
        'component'     : extract_component(html),
        'depends_on'    : extract_depends_on(rss),
        'spkgs'         : extract_spkgs(html),
        'patches'       : patches,
        'authors'       : authors,
        'participants'  : extract_participants(rss),
    }
    if db is not None:
        db.save_ticket(data)
        db_info = db.lookup_ticket(ticket_id)
    return db_info

def extract_tag(sgml, tag):
    """
    Find the first occurance of the tag start (including attributes) and
    return the contents of that tag (really, up until the next end tag
    of that type).
    
    Crude but fast.
    """
    tag_name = tag[1:-1]
    if ' ' in tag_name:
        tag_name = tag_name[:tag_name.index(' ')]
    end = "</%s>" % tag_name
    start_ix = sgml.find(tag)
    if start_ix == -1:
        return None
    end_ix = sgml.find(end, start_ix)
    if end_ix == -1:
        return None
    return sgml[start_ix + len(tag) : end_ix].strip()

def extract_status(html):
    """
    Extracts the status of a ticket from the html page.
    """
    status = extract_tag(html, '<span class="status">')
    if status is None:
        return 'unknown'
    status = status.strip('()')
    status = status.replace('defect', '').replace('enhancement', '').strip()
    return status
    
def extract_priority(html):
    """
    Extracts any spkgs for a ticket from the html page.
    """
    return extract_tag(html, '<td headers="h_priority">')

def extract_component(html):
    return extract_tag(html, '<td headers="h_component">')
    
def extract_title(rss):
    title = extract_tag(rss, '<title>')
    return re.sub(r'.*#\d+:', '', title).strip()

folded_regex = re.compile('all.*(folded|combined|merged)')
subsequent_regex = re.compile('second|third|fourth|next|on top|after')
attachment_regex = re.compile(r"<strong>attachment</strong>\s*set to <em>(.*)</em>", re.M)
rebased_regex = re.compile(r"([-.]?rebased?)|(-v\d)")
def extract_patches(rss):
    """
    Extracts the list of patches for a ticket from the rss feed.
    
    Tries to deduce the subset of attached patches to apply based on
    
        (1) "Apply ..." in comment text
        (2) Mercurial .N naming
        (3) "rebased" in name
        (3) Chronology
    """
    all_patches = []
    patches = []
    authors = {}
    for item in rss.split('<item>'):
        who = extract_tag(item, '<dc:creator>')
        description = extract_tag(item, '<description>').replace('&lt;', '<').replace('&gt;', '>')
        m = attachment_regex.search(description)
        comments = description[description.find('</ul>') + 1:]
        # Look for apply... followed by patch names
        for line in comments.lower().split('\n'):
            if 'apply' in line:
                new_patches = []
                for p in line[line.index('apply'):].split(','):
                    for pp in p.strip().split():
                        if pp in all_patches:
                            new_patches.append(pp)
                if new_patches or (m and not subsequent_regex.search(line)):
                    patches = new_patches
            elif m and folded_regex.search(line):
                patches = [] # will add this patch below
        if m is not None:
            attachment = m.group(1)
            base, ext = os.path.splitext(attachment)
            if '.' in base:
                try:
                    base2, ext2 = os.path.splitext(base)
                    count = int(ext2[1:])
                    for i in range(count):
                        if i:
                            older = "%s.%s%s" % (base2, i, ext)
                        else:
                            older = "%s%s" % (base2, ext)
                        if older in patches:
                            patches.remove(older)
                except:
                    pass
            if rebased_regex.search(attachment):
                older = rebased_regex.sub('', attachment)
                if older in patches:
                    patches.remove(older)
            if ext in ('.patch', '.diff'):
                all_patches.append(attachment)
                patches.append(attachment)
                authors[attachment] = who
    return [(p, authors[p]) for p in patches]

participant_regex = re.compile("<strong>attachment</strong>\w*set to <em>(.*)</em>")
def extract_participants(rss):
    """
    Extracts any spkgs for a ticket from the html page.
    """
    all = set()
    for item in rss.split('<item>'):
        who = extract_tag(item, '<dc:creator>')
        if who:
            all.add(who)
    return list(all)
    
spkg_url_regex = re.compile(r"http://.*?\.spkg")
def extract_spkgs(html):
    """
    Extracts any spkgs for a ticket from the html page.
    
    Just searches for urls ending in .spkg.
    """
    return list(set(spkg_url_regex.findall(html)))


ticket_url_regex = re.compile(r"%s/ticket/(\d+)" % TRAC_URL)
def extract_depends_on(rss):
    depends_on = set()
    rss = rss.lower()
    ix = min(rss.find('depends on'), rss.find('dependency'))
    while ix >= 0:
        line = rss[ix:rss.find('\n', ix)]
        for m in ticket_url_regex.finditer(line):
            depends_on.add(m.group(1))
        ix = min(rss.find('depends on', ix+1), rss.find('dependency', ix+1))
    return list(depends_on)


def do_or_die(cmd):
    print cmd
    res = os.system(cmd)
    if res:
        raise Exception, "%s %s" % (res, cmd)

safe = re.compile('[-+A-Za-z0-9._]*')
def ensure_safe(items):
    """
    Raise an error if item has any spaces in it.
    """
    if isinstance(items, (str, unicode)):
        m = safe.match(items)
        if m is None or m.end() != len(items):
            raise ValueError, "Unsafe patch name '%s'" % items
    else:
        for item in items:
            ensure_safe(item)

def pull_from_trac(sage_root, ticket, branch=None, force=None, interactive=None):
    # Should we set/unset SAGE_ROOT and SAGE_BRANCH here? Fork first?
    if branch is None:
        branch = str(ticket)
    if not os.path.exists('%s/devel/sage-%s' % (sage_root, branch)):
        do_or_die('%s/sage -b main' % (sage_root,))
        do_or_die('%s/sage -clone %s' % (sage_root, branch))
    os.chdir('%s/devel/sage-%s' % (sage_root, branch))
    if interactive:
        raise NotImplementedError
    if not os.path.exists('.hg/patches'):
        do_or_die('hg qinit')
        series = []
    elif not os.path.exists('.hg/patches/series'):
        series = []
    else:
        series = open('.hg/patches/series').read().split('\n')

    desired_series = []
    def append_patch_list(ticket):
        data = scrape(ticket)
        if data['spkgs']:
            raise NotImplementedError, "Spkgs not yet handled."
        if data['depends_on']:
            for dep in data['depends_on']:
                append_patch_list(dep)
        for patch in data['patches']:
            base, hash = patch.split('#')
            desired_series.append((hash, base, get_patch_url(ticket, base)))
    append_patch_list(ticket)
    
    ensure_safe(series)
    ensure_safe(patch for hash, patch, url in desired_series)

    last_good_patch = '-a'
    to_push = list(desired_series)
    for series_patch, (hash, patch, url) in zip(series, desired_series):
        if not series_patch:
            break
        next_hash = digest(open('.hg/patches/%s' % series_patch).read())
#        print next_hash, hash, series_patch
        if next_hash == hash:
            to_push.pop(0)
            last_good_patch = series_patch
        else:
            break

    try:
        if last_good_patch != '-a':
            # In case it's not yet pushed...
            if last_good_patch not in os.popen2('hg qapplied')[1].read().split('\n'):
                do_or_die('hg qpush %s' % last_good_patch)
        do_or_die('hg qpop %s' % last_good_patch)
        for hash, patch, url in to_push:
            if patch in series:
                if not force:
                    raise Exception, "Duplicate patch: %s" % patch
                old_patch = patch
                while old_patch in series:
                    old_patch += '-old'
                do_or_die('hg qrename %s %s' % (patch, old_patch))
            do_or_die('hg qimport %s && hg qpush' % url)
        do_or_die('hg qapplied')
    except:
        os.system('hg qpop -a')
        raise


def push_from_trac(sage_root, ticket, branch=None, force=None, interactive=None):
    raise NotImplementedError



if __name__ == '__main__':
    force = False
    for ticket in sys.argv[1:]:
        if ticket == '-f':
            force = True
            continue
        if '-' in ticket:
            start, end = ticket.split('-')
            tickets = range(int(start), int(end) + 1)
        else:
            tickets = [int(ticket)]
        for ticket in tickets:
            try:
                print ticket, scrape(ticket, force=force)
                time.sleep(1)
            except Exception:
                print "Error for", ticket
                traceback.print_exc()
#    pull_from_trac('/Users/robertwb/sage/current', ticket, force=True)
