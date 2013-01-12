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
import re, os, sys, subprocess, time

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
    exclude_new(r'[^\x00-\x7F]', "Non-ascii characters", **kwds)

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

def startup_time(ticket, loops=3, **kwds):
    try:
        def startup_times(samples, warmups=2):
            all = []
            for k in range(samples + warmups):
                start = time.time()
                do_or_die("$SAGE_ROOT/sage -c ''")
                all.append(time.time() - start)
            all.sort()
            return all[:samples]

        main_timings = []
        ticket_timings = []

        if loops == 0:
            main_timings = [1.2578270435333252, 1.260890007019043, 1.2620019912719727, 1.2620508670806885, 1.2620928287506104, 1.2624049186706543, 1.2628939151763916, 1.2597601413726807, 1.2601690292358398, 1.2619030475616455, 1.2621428966522217, 1.2627081871032715, 1.2631988525390625, 1.263575792312622, 1.2605640888214111, 1.2606971263885498, 1.2611360549926758, 1.2615859508514404, 1.262929916381836, 1.263355016708374, 1.2642560005187988, 1.2611699104309082, 1.2625548839569092, 1.2645201683044434, 1.2647550106048584, 1.2648990154266357, 1.2657241821289062, 1.2662768363952637]
            ticket_timings = [1.3140549659729004, 1.314316987991333, 1.3158237934112549, 1.315997838973999, 1.31626296043396, 1.3165380954742432, 1.317572832107544, 1.3129069805145264, 1.3167099952697754, 1.318160057067871, 1.3189101219177246, 1.3200490474700928, 1.3227341175079346, 1.3227899074554443, 1.314229965209961, 1.3146660327911377, 1.3169920444488525, 1.318953037261963, 1.3190178871154785, 1.3199529647827148, 1.320039987564087, 1.313957929611206, 1.3181397914886475, 1.318274974822998, 1.3190598487854004, 1.3199548721313477, 1.3200139999389648, 1.3249070644378662]

        for _ in range(loops):
            do_or_die("$SAGE_ROOT/sage -b %s > /dev/null" % ticket)
            ticket_timings.extend(startup_times(8, 2))
            do_or_die("$SAGE_ROOT/sage -b main > /dev/null")
            main_timings.extend(startup_times(8, 2))
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
        print "Main:   %0.3g sec (%s samples, std_dev=%0.3g)" % (p1, n1, s1)
        print "Ticket: %0.3g sec (%s samples, std_dev=%0.3g)" % (p2, n2, s2)
        print
        print "Average %s of %0.2g secs or %0.2g%%." % (
            inc_or_dec[increased][:-1], diff, diff / base)
        print

        if increased:
            # swap
            n1, p1, s1, n2, p2, s2 = n2, p2, s2, n1, p1, s1
        err = math.sqrt(s1**2 / n1 + s2**2 / n2)
        stats = []
        for confidence in (.9999, .999, .99, .95, .9, .75):
            lower_bound = (diff - err * ICDF(confidence)) / base
            if lower_bound > 0:
                stats.append((confidence, lower_bound))
            if len(stats) > 4:
                break
        for lower_bound in (1, .5, .1, .05, .01, 0.005, .001):
            confidence = CDF((diff - base * lower_bound) / err)
            if confidence > 0.25:
                stats.append((confidence, lower_bound))
            if len(stats) > 6:
                break

        stats.sort()
        status = PluginResult.Passed
        for confidence, lower_bound, in stats:
            if increased and confidence > .90 and lower_bound > .005:
                status = PluginResult.Failed
            # Get 99.999x%.
            confidence = 1 - float(("%0.1g" if confidence > .9 else "%0.2g") % (1 - confidence))
            print "With %g%% confidence, startup time %s by at least %0.2g%%" % (
                100 * confidence, inc_or_dec[increased], 100 * lower_bound)

        data = dict(stats=stats, main_timings=main_timings, ticket_timings=ticket_timings)
        return PluginResult(status, data=data)

    finally:
        print
        do_or_die("$SAGE_ROOT/sage -b %s > /dev/null" % ticket)


# Some utility functions.

sqrt_pi_over_8 = math.sqrt(math.pi / 8)
def mean(a):
    return 1.0 * sum(a) / len(a)

def std_dev(a):
    xbar = mean(a)
    return math.sqrt(sum((x-xbar)**2 for x in a) / (len(a) - 1.0))

# Aludaat, K.M. and Alodat, M.T. (2008). A note on approximating the normal
# distribution function. Applied Mathematical Sciences, Vol 2, no 9, pgs 425-429.

def CDF(x):
    """
    The cumulative distribution function to within 0.00197323.
    """
    if x < 0:
        return 1 - CDF(-x)
    return 0.5 + 0.5 * math.sqrt(1 - math.exp(-sqrt_pi_over_8 * x*x))

def ICDF(p):
    """
    Inverse cumulative distribution function.
    """
    if p < 0.5:
        return -ICDF(1 - p)
    return math.sqrt(-math.log(1 - (2 * p - 1)**2) / sqrt_pi_over_8)



if __name__ == '__main__':
    plugin = globals()[sys.argv[1]]
    kwds = {}
    for arg in sys.argv[2:]:
        m = re.match("--([a-zA-Z0-9]+)=(([_a-zA-Z]*).*)", arg)
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

