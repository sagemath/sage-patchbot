TRAC_URL = "http://trac.sagemath.org/sage_trac"
TRAC_REPO = "git://trac.sagemath.org/sage.git"

import re
import hashlib
import urllib2
import os
import sys
import tempfile
import traceback
import time
import subprocess
import pprint

from util import (do_or_die, now_str, describe_branch,
                  temp_build_suffix, ensure_free_space,
                  ConfigException, SkipTicket)


def digest(s):
    """
    Compute a cryptographic hash of the string s.
    """
    return hashlib.md5(s).hexdigest()


def get_url(url):
    """
    Return the contents of url as a string.
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
    """
    Should be obsolete now that we use git ?
    """
    if raw:
        return "%s/raw-attachment/ticket/%s/%s" % (TRAC_URL, ticket, patch)
    else:
        return "%s/attachment/ticket/%s/%s" % (TRAC_URL, ticket, patch)


def scrape(ticket_id, force=False, db=None):
    """
    Scrapes the trac page for ``ticket_id``, updating the database if needed.

    If force is ``True``, it will update the database even if the page hash is
    unchanged.

    OUTPUT:

    a dictionary

    This fails if some field contains a TAB character !

    This does not like the unicode titles !

    EXAMPLES::

        sage: scrape(18033)
    """
    ticket_id = int(ticket_id)

    if ticket_id == 0:
        if db is not None:
            db_info = db.lookup_ticket(ticket_id)
            if db_info is not None:
                return db_info
        return {
            'id': ticket_id,
            'title': 'base',
            'page_hash': '0',
            'status': 'base',
            'priority': 'base',
            'component': 'base',
            'depends_on': [],
            'spkgs': [],
            'authors': [],
            'participants': []}

    # hash is defined from the rss of trac page
    rss = get_url("{}/ticket/{}?format=rss".format(TRAC_URL, ticket_id))
    page_hash = digest(rss)

    # First try to use the patchbot database
    if db is not None:
        # TODO: perhaps the db caching should be extracted outside of
        # this function...
        db_info = db.lookup_ticket(ticket_id)
        if not force and db_info is not None and db_info['page_hash'] == page_hash:
            return db_info

    # nothing in the database, now fetch the info from trac server
    tab = get_url("{}/ticket/{}?format=tab".format(TRAC_URL, ticket_id))
    # short_tab = get_url("%s/query?id=%s&format=tab" % (TRAC_URL, ticket_id))
    tsv = parse_tsv(tab)

    # this part is about finding the authors and it needs work !
    authors = set()
    git_commit_of_branch = git_commit(tsv['branch'])
    if tsv['branch'].strip():
        branch = tsv['branch']
        if branch.startswith('u/'):
            authors.add(branch.split('/')[1])
    authors = list(authors)

    authors_fullnames = set()
    for auth in tsv['author'].split(','):
        authors_fullnames.add(auth)
    # this is not working, because at this point the git branch is not 
    # present in the local repo !
    # for auth in authors_from_git_branch(git_commit_of_branch):
    #     authors_fullnames.add(auth)
    authors_fullnames = list(authors_fullnames)

    data = {
        'id': ticket_id,
        'title': tsv['summary'],
        'page_hash': page_hash,
        'status': tsv['status'],
        'resolution': tsv['resolution'],
        'milestone': tsv['milestone'],
        'merged': tsv['merged'],
        'priority': tsv['priority'],
        'component': tsv['component'],
        'depends_on': extract_depends_on(tsv),
        'spkgs': extract_spkgs(tsv),
        'authors': authors,
        'authors_fullnames': authors_fullnames,
        'participants': extract_participants(rss),
        'git_branch': tsv['branch'],
        'git_repo': TRAC_REPO if tsv['branch'].strip() else None,
        'git_commit': git_commit_of_branch,
        'last_activity': now_str(),
    }

    if db is not None:
        db.save_ticket(data)
        db_info = db.lookup_ticket(ticket_id)
        return db_info
    else:
        return data


def authors_from_git_branch(top_commit):
    """
    Return the authors of the code of the given sequence of commits.

    STILL SOME UTF8 problems to solve..

    OUTPUT:

    list of author full names if the branch ``top_commit`` exists locally, and
    an empty list otherwise

    This should be the correct way to find the authors of a ticket !

    to get the names:

    git log --pretty=format:%an base_commit..top_commit

    to get the mails:

    git log --pretty=format:%ae base_commit..top_commit

    but how to map that to trac accounts ??

    EXAMPLES::

        sage: authors_from_git_branch('18498')
        {'Fr\xc3\xa9d\xc3\xa9ric C', 'Nathann C'}
        sage: authors_from_git_branch('15375')
        {'Anne S',
         'Daniel B',
         'Fr\xc3\xa9d\xc3\xa9ric C',
         'Mark S',
         'mshi@math'}
    """
    try:
        base_commit = subprocess.check_output(['git', 'describe', top_commit, '--abbrev=0',
                                               '--tags']).strip()
    except subprocess.CalledProcessError:
        return []
    git_log = subprocess.check_output(['git', 'log', '--pretty=format:%an',
                                       base_commit + '..' + top_commit])
    return set(git_log.splitlines())


def git_commit(branch):
    """
    Retrieve the hash of the commit.

    EXAMPLES::

        sage: git_commit('develop')
        '408796407339cf8ba46d3c5ab9365bdb0f1e456f'
    """
    if branch.strip():
        try:
            return subprocess.check_output(['git', 'ls-remote',
                                            TRAC_REPO, branch]).split()[0]
        except Exception:
            return "unknown"


def parse_tsv(tsv):
    """
    Convert tsv to dict.

    First row gives the names of the fields, second row gives their values.

    OUTPUT:

    a dictionary

    This will fail if some field contains a TAB character.
    """
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
    Find the first occurrence of the tag start (including attributes) and
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
    return sgml[start_ix + len(tag): end_ix].strip()


def extract_participants(rss):
    """
    Extracts any participants for a ticket from the html page.

    This is done using the rss feed.

    This is used in the trust check code for the moment.

    This needs work ! In particular to remove people only in cc if possible!
    """
    all = set()
    for item in rss.split('<item>'):
        who = extract_tag(item, '<dc:creator>')
        if who:
            all.add(who)
    return list(all)

spkg_url_regex = re.compile(r"((?:(?:https?://)|(?:/attachment/)).*?\.(?:spkg|tar\.gz|tar\.bz2))")
# spkg_url_regex = re.compile(r"(?:(?:https?://)|(?:/attachment/))(.*?\.(?:spkg|tar\.gz|tar\.bz2))")
# spkg_url_regex = re.compile(r"(?:(?:https?://)|(?:/attachment/)).*?\.spkg")
#spkg_url_regex = re.compile(r"http://.*?\.spkg")


def extract_spkgs(tsv):
    """
    Extracts any spkgs for a ticket from the html page.

    Just searches for urls ending in .spkg.

    BEWARE: this seems to work only for old-style spkg !
    """
    return list(set(spkg_url_regex.findall(tsv['description'])))


ticket_url_regex = re.compile(r"{}/ticket/(\d+)".format(TRAC_URL))


def extract_depends_on(tsv):
    deps_field = tsv['dependencies']
    deps = []
    for dep in re.finditer(r'#(\d+)', deps_field):
        deps.append(int(dep.group(1)))
    version = re.search(r'sage-\d+(\.\d)+(\.\w+)?', deps_field)
    if version:
        deps.insert(0, version.group(0))
    return deps


def inplace_safe():
    """
    Return whether it is safe to test this ticket inplace.

    This must be called after the merge has succeeded.
    """
    safe = True
    # TODO: Are removed files sufficiently cleaned up?
    cmd = ["git", "diff", "--name-only",
           "patchbot/base..patchbot/ticket_merged"]
    for file in subprocess.check_output(cmd).split('\n'):
        if not file:
            continue
        if (file.startswith("src/sage") or file.startswith("src/doc")
                or file in ("src/setup.py", "src/module_list.py",
                            "README.txt", ".gitignore")):
            continue
        else:
            print "Unsafe file:", file
            safe = False
    return safe


def pull_from_trac(sage_root, ticket_id, branch=None, force=None,
                   interactive=None, inplace=None, use_ccache=False,
                   safe_only=False):
    """
    Create four branches from base and ticket.

    If ticket deemed unsafe then clone git repo to temp directory. ?!

    Additionally, if ``use_ccache`` then install ccache. Set some global
    and environment variables.

    There are four branches at play here:

    patchbot/base -- the latest release that all tickets are merged into for testing
    patchbot/base_upstream -- temporary staging area for patchbot/base
    patchbot/ticket_upstream -- pristine clone of the ticket on trac
    patchbot/ticket_merged -- merge of patchbot/ticket_upstream into patchbot/base
    """
    merge_failure = False
    is_safe = False
    try:
        os.chdir(sage_root)
        info = scrape(ticket_id)
        ensure_free_space(sage_root)
        do_or_die("git checkout patchbot/base")
        if ticket_id == 0:
            do_or_die("git branch -f patchbot/ticket_upstream patchbot/base")
            do_or_die("git branch -f patchbot/ticket_merged patchbot/base")
            return
        branch = info['git_branch']
        repo = info['git_repo']
        do_or_die("git fetch %s +%s:patchbot/ticket_upstream" % (repo, branch))
        base = describe_branch('patchbot/ticket_upstream', tag_only=True)
        do_or_die("git rev-list --left-right --count %s..patchbot/ticket_upstream" % base)
        do_or_die("git branch -f patchbot/ticket_merged patchbot/base")
        do_or_die("git checkout patchbot/ticket_merged")
        try:
            do_or_die("git merge -X patience patchbot/ticket_upstream")
        except Exception:
            do_or_die("git merge --abort")
            merge_failure = True
            raise
        is_safe = inplace_safe()
        if not is_safe:
            if safe_only:
                raise SkipTicket("unsafe")
            tmp_dir = tempfile.mkdtemp(temp_build_suffix + str(ticket_id))
            ensure_free_space(tmp_dir)
            do_or_die("git clone . '{}'".format(tmp_dir))
            os.chdir(tmp_dir)
            os.symlink(os.path.join(sage_root, "upstream"), "upstream")
            os.environ['SAGE_ROOT'] = tmp_dir
            do_or_die("git branch -f patchbot/base remotes/origin/patchbot/base")
            do_or_die("git branch -f patchbot/ticket_upstream remotes/origin/patchbot/ticket_upstream")
            if use_ccache:
                if not os.path.exists('logs'):
                    os.mkdir('logs')
                do_or_die("./sage -i ccache")
    except Exception, exn:
        if merge_failure or (not is_safe):
            raise
        else:
            raise ConfigException(exn.message)


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
