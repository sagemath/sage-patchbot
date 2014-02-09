TRAC_URL = "http://trac.sagemath.org/sage_trac"
TRAC_REPO = "http://trac.sagemath.org/sage.git"

import re, hashlib, urllib2, os, sys, tempfile, traceback, time, subprocess
import pprint

from util import do_or_die, compare_version, get_version, now_str, git_commit

def digest(s):
    """
    Computes a cryptographic hash of the string s.
    """
    return hashlib.md5(s).hexdigest()

def get_url(url):
    """
    Returns the contents of url as a string.
    """
    try:
        url = url.replace(' ', '%20')
        handle = urllib2.urlopen(url, timeout=15)
        data = handle.read()
        handle.close()
        return data
    except:
        print url
        raise

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
    if ticket_id == 0:
        if db is not None:
            db_info = db.lookup_ticket(ticket_id)
            if db_info is not None:
                return db_info
        return {
            'id'            : ticket_id,
            'title'         : 'base',
            'page_hash'     : '0',
            'status'        : 'base',
            'priority'      : 'base',
            'component'     : 'base',
            'depends_on'    : [],
            'spkgs'         : [],
            'patches'       : [],
            'authors'       : [],
            'participants'  : [],
        }

    rss = get_url("%s/ticket/%s?format=rss" % (TRAC_URL, ticket_id))
    tsv = parse_tsv(get_url("%s/ticket/%s?format=tab" % (TRAC_URL, ticket_id)))
    page_hash = digest(rss) # rss isn't as brittle
    if db is not None:
        # TODO: perhaps the db caching should be extracted outside of this function...
        db_info = db.lookup_ticket(ticket_id)
        if not force and db_info is not None and db_info['page_hash'] == page_hash:
            return db_info
    authors = set()
    patches = []
    if tsv['branch'].strip():
        # TODO: query history
        branch = tsv['branch']
        if branch.startswith('u/'):
            authors.add(branch.split('/')[1])
    else:
        for patch, who in extract_patches(rss):
            authors.add(who)
            patches.append(patch + "#" + digest(get_patch(ticket_id, patch)))
    authors = list(authors)
    data = {
        'id'            : ticket_id,
        'title'         : tsv['summary'],
        'page_hash'     : page_hash,
        'status'        : tsv['status'],
        'resolution'    : tsv['resolution'],
        'milestone'     : tsv['milestone'],
        'merged'        : tsv['merged'],
        'priority'      : tsv['priority'],
        'component'     : tsv['component'],
        'depends_on'    : extract_depends_on(tsv),
        'spkgs'         : extract_spkgs(tsv),
        'patches'       : patches,
        'authors'       : authors,
        'participants'  : extract_participants(rss),
        'git_branch'    : tsv['branch'],
        'git_repo'      : TRAC_REPO if tsv['branch'].strip() else None,
        'git_commit'    : git_commit(tsv['branch']),
        'last_activity' : now_str(),
    }
    if db is not None:
        db.save_ticket(data)
        db_info = db.lookup_ticket(ticket_id)
        return db_info
    else:
        return data

def git_commit(branch):
    if branch.strip():
        try:
            return subprocess.check_output(['git', 'ls-remote', TRAC_REPO, branch]).split()[0]
        except Exception:
            return "unknown"

def parse_tsv(tsv):
    header, data = tsv.split('\n', 1)
    def sanitize(items):
        for item in items:
            item = item.strip().replace('""', '"')
            if item and item[0] == '"' and item[-1] == '"':
                item = item[1:-1]
            yield item
    return dict(zip(sanitize(header.split('\t')),
                    sanitize(data.split('\t'))))

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
        for line in comments.split('\n'):
            if 'apply' in line.lower():
                new_patches = []
                for p in line[line.lower().index('apply') + 5:].split(','):
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

spkg_url_regex = re.compile(r"(?:(?:https?://)|(?:/attachment/)).*?\.spkg")
#spkg_url_regex = re.compile(r"http://.*?\.spkg")
def extract_spkgs(tsv):
    """
    Extracts any spkgs for a ticket from the html page.

    Just searches for urls ending in .spkg.
    """
    return list(set(spkg_url_regex.findall(tsv['description'])))

def min_non_neg(*rest):
    non_neg = [a for a in rest if a >= 0]
    if len(non_neg) == 0:
        return rest[0]
    elif len(non_neg) == 1:
        return non_neg[0]
    else:
        return min(*non_neg)

ticket_url_regex = re.compile(r"%s/ticket/(\d+)" % TRAC_URL)
def extract_depends_on(tsv):
    deps_field = tsv['dependencies']
    deps = []
    for dep in re.finditer(r'#(\d+)', deps_field):
        deps.append(int(dep.group(1)))
    version = re.search(r'sage-\d+(\.\d)+(\.\w+)?', deps_field)
    if version:
        deps.insert(0, version.group(0))
    return deps



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

def inplace_safe():
    """
    Returns whether it is safe to test this ticket inplace.
    """
    safe = True
    # TODO: Are removed files sufficiently cleaned up?
    for file in subprocess.check_output(["git", "diff", "--name-only", "patchbot/base..patchbot/ticket_merged"]).split('\n'):
        if not file:
            continue
        if file.startswith("src/sage") or file in ("src/setup.py", "src/module_list.py", "README.txt", ".gitignore"):
            continue
        else:
            print "Unsafe file:", file
            safe = False
    return safe

def pull_from_trac(sage_root, ticket, branch=None, force=None, interactive=None, inplace=None, use_ccache=False):
    # There are four branches at play here:
    # patchbot/base -- the latest release that all tickets are merged into for testing
    # patchbot/base_upstream -- temporary staging area for patchbot/base
    # patchbot/ticket_upstream -- pristine clone of the ticket on trac
    # patchbot/ticket_merged -- merge of patchbot/ticket_upstream into patchbot/base
    ticket_id = ticket
    info = scrape(ticket_id)
    os.chdir(sage_root)
    do_or_die("git checkout patchbot/base")
    if ticket_id == 0:
        do_or_die("git branch -f patchbot/ticket_upstream patchbot/base")
        do_or_die("git branch -f patchbot/ticket_merged patchbot/base")
        return
    branch = info['git_branch']
    repo = info['git_repo']
    do_or_die("git fetch %s +%s:patchbot/ticket_upstream" % (repo, branch))
    do_or_die("git rev-list --left-right --count patchbot/base..patchbot/ticket_upstream")
    do_or_die("git branch -f patchbot/ticket_merged patchbot/base")
    do_or_die("git checkout patchbot/ticket_merged")
    try:
        do_or_die("git merge -X patience patchbot/ticket_upstream")
    except Exception:
        do_or_die("git merge --abort")
        raise
    if not inplace_safe():
        tmp_dir = tempfile.mkdtemp("-sage-git-temp-%s" % ticket_id)
        do_or_die("git clone . '%s'" % tmp_dir)
        os.chdir(tmp_dir)
        os.symlink(os.path.join(sage_root, "upstream"), "upstream")
        os.environ['SAGE_ROOT'] = tmp_dir
        do_or_die("git branch -f patchbot/base remotes/origin/patchbot/base")
        do_or_die("git branch -f patchbot/ticket_upstream remotes/origin/patchbot/ticket_upstream")
        if use_ccache:
            do_or_die("./sage -i ccache")


def push_from_trac(sage_root, ticket, branch=None, force=None, interactive=None):
    raise NotImplementedError



if __name__ == '__main__':
    force = False
    apply = False
    for ticket in sys.argv[1:]:
        if ticket == '-f':
            force = True
            continue
        if ticket == '-a':
            apply = True
            continue
        if '-' in ticket:
            start, end = ticket.split('-')
            tickets = range(int(start), int(end) + 1)
        else:
            tickets = [int(ticket)]
        for ticket in tickets:
            try:
                print ticket
                pprint.pprint(scrape(ticket, force=force))
                if apply:
                    pull_from_trac(os.environ['SAGE_ROOT'], ticket, force=True)
                time.sleep(1)
            except Exception:
                print "Error for", ticket
                traceback.print_exc()
        force = apply = False
#    pull_from_trac('/Users/robertwb/sage/current', ticket, force=True)
