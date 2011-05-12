"""
A plugin is any callable. 

It is called after the ticket has been successfully applied and built,
but before tests are run. It should print out any analysis to stdout,
raising an exception if anything went wrong.

The parameters are as follows: 

   ticket -- a dictionary of all the ticket informaton
   original_dir -- pristine sage-main directory
   patched_dir -- patched sage-branch directory for this ticket
   patch_list -- a list of absolute paths to the patch files for this ticket
   
It is recommended that a plugin ignore extra keywords to be 
compatible with future options.
"""

from trac import do_or_die

def coverage(ticket, **kwds):
    do_or_die('$SAGE_ROOT/sage -coverageall')

def docbuild(ticket, **kwds):
    do_or_die('$SAGE_ROOT/sage -docbuild --jsmath reference html')
