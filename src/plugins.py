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

def coverage(ticket, sage_binary, **kwds):
    all = subprocess.check_output([sage_binary, '-coverageall'])
    print all

def docbuild(ticket, **kwds):
    do_or_die('$SAGE_ROOT/sage -docbuild --jsmath reference html')

def trailing_whitespace(ticket, patches, **kwds):
    ignore_empty = True
    bad_lines = 0
    trailing = re.compile("\\+.*\\s+$")
    for patch_path in patches:
        patch = os.path.basename(patch_path)
        print patch
        for ix, line in enumerate(open(patch_path)):
            line = line.strip("\n")
            m = trailing.match(line)
            if m:
                print "    %s:%s %s$" % (patch, ix+1, line)
                if line.strip() == '+' and ignore_empty:
                    pass
                else:
                    bad_lines += 1
    msg = "Trailing whitespace inserted on %s %slines." % (bad_lines, "non-empty " if ignore_empty else "")
    print msg
    if bad_lines > 0:
        raise ValueError(msg)

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
        new = module_set.difference(baseline_set)
        removed = baseline_set.difference(module_set)
        if new:
            status = PluginResult.Failed
            print "New:", ", ".join(new)
        else:
            status = PluginResult.Passed
        if removed:
            print "Removed:", ", ".join(removed)
        data = {'new': new, 'removed': removed}

    print
#    print '\n'.join(modules)
    return PluginResult(status, baseline=modules, data=data)

if __name__ == '__main__':
    plugin = globals()[sys.argv[1]]
    plugin(-1, patches=sys.argv[2:])
