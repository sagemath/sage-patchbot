"""
A plugin is any callable. 

It is called after the ticket has been successfully applied and built,
but before tests are run. It should print out any analysis to stdout,
raising an exception if anything went wrong.  Alternatively, it may return
a PluginResult indicating success or failure, along with other data.

The parameters are as follows: 

   ticket -- a dictionary of all the ticket informaton
   original_dir -- pristine sage-main directory
   patched_dir -- patched sage-branch directory for this ticket
   patchs -- a list of absolute paths to the patch files for this ticket
   sage_binary -- the path to $SAGE_ROOT/sage
   baseline -- if a PluginResult was returned with a baseline for ticket 0,
               it will be returned here for comparison
   
It is recommended that a plugin ignore extra keywords to be 
compatible with future options.
"""

import re, os, sys, subprocess

from trac import do_or_die

class PluginResult:
    Passed = "Passed"
    Failed = "Failed"
    def __init__(self, status, data=None, baseline=None):
        assert status in (self.Passed, self.Failed)
        self.status = status
        self.data = data
        self.baseline = baseline or data

def coverage(ticket, sage_binary, baseline=None, **kwds):
    # TODO: This doesn't check that tests were added to existing doctests for
    # new functionality.
    all = subprocess.check_output([sage_binary, '-coverageall'])
    current = {}
    total_funcs = 0
    total_docs = 0
    status = "Passed"
    def format(docs, funcs, prec=None):
        if funcs == 0:
            return "N/A"
        else:
            percent = 100.0 * docs / funcs
            if prec is None:
                percent = int(percent)
            else:
                percent = ("%%0.%sf" % prec) % percent
            return "%s / %s = %s%%" % (docs, funcs, percent)
    for line in all.split('\n'):
        m = re.match(r"(.*): .*\((\d+) of (\d+)\)", line)
        if m:
            module, docs, funcs = m.groups()
            docs = int(docs)
            funcs = int(funcs)
            current[module] = docs, funcs
            total_docs += docs
            total_funcs += funcs
            if baseline:
                old_docs, old_funcs = baseline.get(module, (0, 0))
                if old_funcs == 0:
                    if funcs != docs:
                        print "Missing doctests ", module, format(docs, funcs)
                        status = "Failed"
                    else:
                        print "Full doctests ", module, format(docs, funcs)
                elif funcs - docs > old_funcs - old_docs:
                    print     "Decreased doctests", module, "from", format(old_docs, old_funcs), "to", format(docs, funcs)
                    status = "Failed"
                elif funcs - docs < old_funcs - old_docs:
                    print     "Increased doctests", module, "from", format(old_docs, old_funcs), "to", format(docs, funcs)

    current[None] = total_docs, total_funcs
    if baseline:
        print
        if baseline[None] == current[None]:
            print "Coverage remained unchanged."
        else:
            print "Coverage went from", format(*baseline[None], prec=3), "to", format(*current[None], prec=3)
        data = sorted(set(current.items()) - set(baseline.items()))
    else:
        data = None

    if baseline:
        print
        print "=" * 20
    print
    print all
    
    return PluginResult(status, baseline=current, data=data)

def docbuild(ticket, **kwds):
    do_or_die('$SAGE_ROOT/sage -docbuild --jsmath reference html')

def exclude_new(regex, msg, ticket, patches, **kwds):
    ignore_empty = True
    bad_lines = 0
    bad = re.complie(r'\+.*' + regex)
    for patch_path in patches:
        patch = os.path.basename(patch_path)
        print patch
        for ix, line in enumerate(open(patch_path)):
            line = line.strip("\n")
            m = non_ascii.match(line)
            if m:
                print "    %s:%s %s$" % (patch, ix+1, line)
                if line.strip() == '+' and ignore_empty:
                    pass
                else:
                    bad_lines += 1
    full_msg = "%s inserted on %s %slines" % (
        msg, bad_lines, "non-empty " if ignore_empty else "")
    print full_msg
    if bad_lines > 0:
        raise ValueError(full_msg)

def trailing_whitespace(ticket, patches, **kwds):
    exclude_new(r'\s+$', "Trailing whitespace", **kwds)

def non_ascii(**kwds):
    exclude_new(regex=r'[^\x00-\x7F]', "Non-ascii characters", **kwds)

def commit_messages(ticket, patches, **kwds):
    for patch_path in patches:
        patch = os.path.basename(patch_path)
        print "Looking at", patch
        header = []
        for line in open(patch_path):
            if line.startswith('diff '):
                break
            header.append(line)
        else:
            print ''.join(header[:10])
            raise ValueError("Not a valid patch file: " + patch)
        print ''.join(header)
        if header[0].strip() != "# HG changeset patch":
            raise ValueError("Not a mercurial patch file: " + patch)
        for line in header:
            if not line.startswith('# '):
                # First description line
                if line.startswith('[mq]'):
                    raise ValueError("Mercurial queue boilerplate")
                elif not re.search(r"\b%s\b" % ticket['id'], line):
                    print "Ticket number not in first line of comments: " + patch
                break
        else:
            raise ValueError("No patch comments:" + patch)
        print
    print "All patches good."

def startup_modules(ticket, sage_binary, baseline=None, **kwds):
    # Sometimes the first run does something different...
    do_or_die("time $SAGE_ROOT/sage -c ''")
    # Print out all the modules imported at startup.
    modules = subprocess.check_output([sage_binary, "-c", r"print '\n'.join(sorted(sys.modules.keys()))"]).split('\n')

    print
    print "Total count:", len(modules)
    print
    if baseline is None:
        status = PluginResult.Passed
        data = {}
    else:
        module_set = set(modules)
        baseline_set = set(baseline)
        new = sorted(module_set - baseline_set)
        removed = sorted(baseline_set - module_set)
        if new:
            status = PluginResult.Failed
            print "New:"
            print "    " + "\n    ".join(new)
        else:
            status = PluginResult.Passed
        if removed:
            print "Removed:"
            print "    " + "\n    ".join(removed)
        data = {'new': new, 'removed': removed}

    if baseline:
        print
        print "=" * 20
    print
    print '\n'.join(modules)
    return PluginResult(status, baseline=modules, data=data)


if __name__ == '__main__':
    plugin = globals()[sys.argv[1]]
    plugin(-1, patches=sys.argv[2:])
