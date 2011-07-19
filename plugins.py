"""
A plugin is any callable. 

It is called after the ticket has been successfully applied and built,
but before tests are run. It should print out any analysis to stdout,
raising an exception if anything went wrong.

The parameters are as follows: 

   ticket -- a dictionary of all the ticket informaton
   original_dir -- pristine sage-main directory
   patched_dir -- patched sage-branch directory for this ticket
   patchs -- a list of absolute paths to the patch files for this ticket
   
It is recommended that a plugin ignore extra keywords to be 
compatible with future options.
"""

import re, os

from trac import do_or_die


def coverage(ticket, **kwds):
    do_or_die('$SAGE_ROOT/sage -coverageall')

def docbuild(ticket, **kwds):
    do_or_die('$SAGE_ROOT/sage -docbuild --jsmath reference html')

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
                    raise ValueError("Ticket number not in first line of comments:" + patch)
                break
        else:
            raise ValueError("No patch comments:" + patch)
        print
    print "All patches good."
