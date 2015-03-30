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
import math
import re
import os
import sys
import subprocess
import time

from trac import do_or_die
from util import describe_branch


class PluginResult:
    Passed = "Passed"
    Failed = "Failed"

    def __init__(self, status, data=None, baseline=None):
        assert status in (self.Passed, self.Failed)
        self.status = status
        self.data = data
        self.baseline = baseline or data


def git_rev_list(ticket, **kwds):
    if str(ticket['id']) != '0':
        base_only = int(subprocess.check_output(["git", "rev-list", "--count", "patchbot/ticket_upstream..patchbot/base"]))
        ticket_only = int(subprocess.check_output(["git", "rev-list", "--count", "patchbot/base..patchbot/ticket_upstream"]))
        print "only in ticket (%s)" % ticket_only
        print "only in base (%s)" % base_only
        print
        base = describe_branch('patchbot/ticket_upstream', tag_only=True)
        do_or_die("git diff --stat %s..patchbot/ticket_upstream" % base)
        print
        do_or_die("git log --oneline %s..patchbot/ticket_upstream" % base)
        print
        print
        do_or_die("git log %s..patchbot/ticket_upstream" % base)


def coverage(ticket, sage_binary, baseline=None, **kwds):
    """
    TODO: This doesn't check that tests were added to existing doctests for
    new functionality.
    """
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
                    print "Decreased doctests", module, "from", format(old_docs, old_funcs), "to", format(docs, funcs)
                    status = "Failed"
                elif funcs - docs < old_funcs - old_docs:
                    print "Increased doctests", module, "from", format(old_docs, old_funcs), "to", format(docs, funcs)

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
    do_or_die('make doc-clean')
    do_or_die('make doc')
    doc_log = 'logs/dochtml.log'
    if os.path.exists(doc_log):
        r = subprocess.call(['grep', 'WARNING|SEVERE|ERROR|make.*Error|Exception occurred|Sphinx error|Segmentation fault', doc_log])
        if r != 1:
            # grep returns 1 iff there were no matches
            raise ValueError


def exclude_new(ticket, regex, msg, **kwds):
    """
    Search in new code for patterns that should be avoided.

    See the next functions for several such patterns.
    """
    ignore_empty = True
    bad_lines = 0
    if regex[0] == '^':
        bad = re.compile(r'\+' + regex[1:])
    else:
        bad = re.compile(r'\+.*' + regex)
    for line in subprocess.Popen(['git', 'diff', 'patchbot/base..patchbot/ticket_merged'], stdout=subprocess.PIPE).stdout:
        if line[:3] in ('+++', '---', '@@ '):
            print line
        else:
            line = line.strip("\n")
            m = bad.match(line)
            if m:
                print line
                if line.strip() == '+' and ignore_empty:
                    pass
                else:
                    bad_lines += 1
    full_msg = "%s inserted on %s %slines" % (
        msg, bad_lines, "non-empty " if ignore_empty else "")
    print full_msg
    if bad_lines > 0:
        raise ValueError(full_msg)


def trailing_whitespace(ticket, **kwds):
    """
    Look for the presence of trailing whitespaces.
    """
    exclude_new(ticket, regex=r'\s+$', msg="Trailing whitespace", **kwds)


def non_ascii(ticket, **kwds):
    """
    Look for the presence of non-ascii characters.
    """
    exclude_new(ticket, regex=r'[^\x00-\x7F]',
                msg="Non-ascii characters", **kwds)


def doctest_continuation(ticket, **kwds):
    """
    Make sure that doctest continuation use syntax `....:`.
    """
    exclude_new(ticket, regex=r'^\s*\.\.\.\s',
                msg="Old-style doctest continuation", **kwds)


def raise_statements(ticket, **kwds):
    """
    Make sure that raise statements use python3 syntax.
    """
    exclude_new(ticket, regex=r'^\s*raise\s*[A-Za-z]*Error,',
                msg="Old-style raise statement", **kwds)


def commit_messages(ticket, patches, is_git=False, **kwds):
    """
    Check for the existence of a commit message.

    This was for patches, obsolete now ?
    """
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
        if not is_git:
            if header[0].strip() != "# HG changeset patch":
                raise ValueError("Not a mercurial patch file: " + patch)
            for line in header:
                if not line.startswith('# '):
                    # First description line
                    if line.startswith('[mq]'):
                        raise ValueError("Mercurial queue boilerplate")
                    # elif not re.search(r"\b%s\b" % ticket['id'], line):
                    #     print "Ticket number not in first line of comments: " + patch
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


def startup_time(ticket, original_dir, patched_dir, loops=5,
                 total_samples=50, dry_run=False, **kwds):
    if dry_run:
        loops //= 2
        total_samples //= 5
    print total_samples, "samples in", loops, "loops"
    ticket_id = ticket['id']
    choose_base = "git checkout patchbot/base; make build > /dev/null"
    choose_ticket = "git checkout patchbot/ticket_merged; make build  > /dev/null"
    try:

        def startup_times(samples):
            do_or_die("$SAGE_ROOT/sage -c ''")
            all = []
            for k in range(samples):
                start = time.time()
                do_or_die("$SAGE_ROOT/sage -c ''")
                all.append(time.time() - start)
            return all

        main_timings = []
        ticket_timings = []

        os.chdir(patched_dir)
        do_or_die(choose_ticket)
        do_or_die("$SAGE_ROOT/sage -c ''")
        os.chdir(original_dir)
        do_or_die(choose_base)
        do_or_die("$SAGE_ROOT/sage -c ''")

        for k in range(loops):
            os.chdir(patched_dir)
            do_or_die(choose_ticket)
            ticket_timings.extend(startup_times(total_samples //
                                                loops + 2 * k - loops + 1))
            os.chdir(original_dir)
            do_or_die(choose_base)
            main_timings.extend(startup_times(total_samples //
                                              loops + 2 * k - loops + 1))
        print "main_timings =", main_timings
        print "ticket_timings =", ticket_timings

        n1 = len(main_timings)
        p1 = mean(main_timings)
        s1 = std_dev(main_timings)

        n2 = len(ticket_timings)
        p2 = mean(ticket_timings)
        s2 = std_dev(ticket_timings)

        base = p1
        diff = abs(p2 - p1)
        increased = p1 < p2
        inc_or_dec = ['decreased', 'increased']

        print
        print "Main:   %0.5g sec (%s samples, std_dev=%0.3g)" % (p1, n1, s1)
        print "Ticket: %0.5g sec (%s samples, std_dev=%0.3g)" % (p2, n2, s2)
        print
        print "Average %s of %0.2g secs or %0.2g%%." % (
            inc_or_dec[increased][:-1], diff, 100 * diff / base)
        print
        print "Using the Mann-Whitney U test to determine significance."

        if increased:
            # swap
            n1, p1, s1, n2, p2, s2 = n2, p2, s2, n1, p1, s1
        z = mann_whitney_U(main_timings, ticket_timings)
        confidence_intervals = []
        for lower_bound in (1, .5, .25, .1, .05, .025, .01, 0.005, .0025, .001):
            z = mann_whitney_U(main_timings, ticket_timings,
                               offset=base * lower_bound)
            confidence = CDF(z)
            if confidence > 0.25:
                confidence_intervals.append((confidence, lower_bound))
            if len(confidence_intervals) >= 5:
                break

        status = PluginResult.Passed
        if not confidence_intervals:
            print "No statistically significant difference."
        else:
            print "May have caused a slowdown."
        for confidence, lower_bound, in confidence_intervals:
            if increased and confidence >= .95 and lower_bound >= .001:
                status = PluginResult.Failed
            # Print 99.999x%.
            confidence = 1 - float(("%0.1g" if confidence > .9
                                    else "%0.2g") % (1 - confidence))
            print "With %g%% confidence, startup time %s by at least %0.2g%%" % (
                100 * confidence, inc_or_dec[increased], 100 * lower_bound)

        if not increased:
            confidence_intervals = [(x, -y) for x, y in confidence_intervals]
        data = dict(confidence_intervals=confidence_intervals,
                    main_timings=main_timings, ticket_timings=ticket_timings,
                    loops=loops, total_samples=total_samples)
        if str(ticket_id) == '0':
            # Never fail the initial startup.
            status = PluginResult.Passed
        return PluginResult(status, data=data)

    finally:
        print
        os.chdir(patched_dir)
        do_or_die(choose_ticket)


# Some utility functions.


def mann_whitney_U(a, b, offset=0):
    all = [(x, 0) for x in a] + [(x - offset, 1) for x in b]
    all.sort()
    R = [0, 0]
    for ix, (x, k) in enumerate(all):
        R[k] += ix + 1
    n0 = len(a)
    n1 = len(b)
    U = [R[0] - n1 * (n0 + 1) / 2, R[1] - n1 * (n1 + 1) / 2]
    mU = n0 * n1 / 2
    sU = math.sqrt(n0 * n1 * (n0 + n1 + 1) / 12.0)
    return (U[1] - mU) / sU

sqrt_pi_over_8 = math.sqrt(math.pi / 8)


def mean(a):
    return 1.0 * sum(a) / len(a)


def std_dev(a):
    xbar = mean(a)
    return math.sqrt(sum((x - xbar) ** 2 for x in a) / (len(a) - 1.0))

# Aludaat, K.M. and Alodat, M.T. (2008). A note on approximating the
# normal distribution function. Applied Mathematical Sciences, Vol 2,
# no 9, pgs 425-429.


def CDF(x):
    """
    The cumulative distribution function to within 0.00197323.
    """
    if x < 0:
        return 1 - CDF(-x)
    return 0.5 + 0.5 * math.sqrt(1 - math.exp(-sqrt_pi_over_8 * x * x))


def ICDF(p):
    """
    Inverse cumulative distribution function.
    """
    if p < 0.5:
        return -ICDF(1 - p)
    return math.sqrt(-math.log(1 - (2 * p - 1) ** 2) / sqrt_pi_over_8)


if __name__ == '__main__':
    plugin = globals()[sys.argv[1]]
    kwds = {}
    for arg in sys.argv[2:]:
        m = re.match("--([_a-zA-Z0-9]+)=(([_a-zA-Z]*).*)", arg)
        if not m:
            print arg, "must be of the form --kwd=expr"
            sys.exit(1)
        key = m.group(1)
        if m.group(2) == m.group(3):
            value = m.group(2)
        else:
            value = eval(m.group(2))
        kwds[key] = value
    plugin(**kwds)
