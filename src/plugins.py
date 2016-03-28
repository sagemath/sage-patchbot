"""
A plugin is any callable.

It is called after the ticket has been successfully applied and built,
but before tests are run. It should print out any analysis to stdout,
raising an exception if anything went wrong.  Alternatively, it may return
a PluginResult indicating success or failure, along with other data.

The parameters are as follows:

- ticket -- a dictionary of all the ticket information
- sage_binary -- the path to $SAGE_ROOT/sage
- baseline -- if a PluginResult was returned with a baseline for ticket 0,
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
        print("only in ticket ({})".format(ticket_only))
        print("only in base ({})".format(base_only))
        base = describe_branch('patchbot/ticket_upstream', tag_only=True)
        do_or_die("git diff --stat %s..patchbot/ticket_upstream" % base)
        do_or_die("git log --oneline %s..patchbot/ticket_upstream" % base)
        do_or_die("git log %s..patchbot/ticket_upstream" % base)


def coverage(ticket, sage_binary, baseline=None, **kwds):
    """
    TODO: This does not check that tests were added to existing doctests for
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
            return "{} / {} = {}%".format(docs, funcs, percent)
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
                        msg = "Missing doctests in {}: {}"
                        print(msg.format(module, format(docs, funcs)))
                        status = "Failed"
                    else:
                        msg = "Full doctests in {}: {}"
                        print(msg.format(module, format(docs, funcs)))
                elif funcs - docs > old_funcs - old_docs:
                    msg = "Decreased doctests in {}: from {} to {}"
                    print(msg.format(module, format(old_docs, old_funcs),
                                     format(docs, funcs)))
                    status = "Failed"
                elif funcs - docs < old_funcs - old_docs:
                    msg = "Increased doctests in{}: from {} to {}"
                    print(msg.format(module, format(old_docs, old_funcs),
                                     format(docs, funcs)))

    current[None] = total_docs, total_funcs
    if baseline:
        if baseline[None] == current[None]:
            print("Coverage remained unchanged.")
        else:
            msg = "Coverage went from {} to {}"
            print(msg.format(format(*baseline[None], prec=3),
                             format(*current[None], prec=3)))
        data = sorted(set(current.items()) - set(baseline.items()))
    else:
        data = None

    if baseline:
        print("=" * 20)
    print(all)

    return PluginResult(status, baseline=current, data=data)


def docbuild(ticket, make, **kwds):
    """
    Build the documentation.
    """
    do_or_die('{} doc'.format(make))


def docbuild_pdf(ticket, make, **kwds):
    """
    Build the PDF documentation.

    This requires a very complete LaTeX installation.

    It may report false failures if some LaTeX packages are missing.

    STILL EXPERIMENTAL!
    """
    do_or_die('{} doc-pdf'.format(make))


def exclude_new_file_by_file(ticket, regex, file_condition, msg, **kwds):
    """
    Search in new code for patterns that should be avoided.

    The pattern in given by a regular expression `regex`. See the next
    functions `trailing_whitespace`, `non_ascii`, etc for several such
    patterns.

    Proceeding file by file, it will only look inside the files that
    pass the chosen file condition.

    .. SEEALSO:: exclude_new

    This could be useful to check for unicode declaration.
    """
    changed_files = list(subprocess.Popen(['git', 'diff', '--name-only', 'patchbot/base..patchbot/ticket_merged'], stdout=subprocess.PIPE).stdout)
    changed_files = [f.strip("\n") for f in changed_files]

    bad_lines = 0
    for file in changed_files:
        try:
            if file_condition(file):
                gitdiff = list(subprocess.Popen(['git', 'diff', 'patchbot/base..patchbot/ticket_merged', file], stdout=subprocess.PIPE).stdout)
                bad_lines += exclude_new_in_diff(gitdiff, regex)
        except IOError:  # file has been deleted
            pass

    full_msg = "{} inserted on {} non-empty lines"
    full_msg = full_msg.format(msg, bad_lines)
    print(full_msg)
    if bad_lines:
        raise ValueError(full_msg)


def exclude_new(ticket, regex, msg, **kwds):
    """
    Search in new code for patterns that should be avoided.

    The pattern in given by a regular expression.

    See the next functions `trailing_whitespace`, `non_ascii`, etc
    for several such patterns.

    Proceeding just once for all the changed files.

    .. SEEALSO:: exclude_new_file_by_file
    """
    gitdiff = subprocess.Popen(['git', 'diff',
                                'patchbot/base..patchbot/ticket_merged'],
                               stdout=subprocess.PIPE).stdout
    bad_lines = exclude_new_in_diff(gitdiff, regex)
    full_msg = "{} inserted on {} non-empty lines"
    full_msg = full_msg.format(msg, bad_lines)
    print(full_msg)
    if bad_lines:
        raise ValueError(full_msg)


def exclude_new_in_diff(gitdiff, regex):
    """
    Search in the given diff for patterns that should be avoided.

    The pattern in given by a regular expression, for example r'\:\:\:$'

    See the next functions `trailing_whitespace`, `non_ascii`, etc
    for several such patterns.
    """
    # looking for the regular expression 'regex' only in the added lines
    if regex[0] == '^':
        bad = re.compile(r'\+' + regex[1:])
    else:
        bad = re.compile(r'\+.*' + regex)

    bad_lines = 0
    for line in gitdiff:
        line = line.strip()
        if line[:3] == '---' or line == '+':
            pass
        elif line[:3] == '+++':
            file_line = 'inside file: ' + line[3:]
            file_line_printed = False
        elif line[:3] == '@@ ':
            pos_line = line
            pos_line_printed = False
        elif bad.match(line):
            if not file_line_printed:
                print(file_line)
                file_line_printed = True
            if not pos_line_printed:
                print(pos_line)
                pos_line_printed = True
            print(line)
            bad_lines += 1
    return bad_lines


def trailing_whitespace(ticket, **kwds):
    """
    Look for the presence of trailing whitespaces.
    """
    exclude_new(ticket, regex=r'\s+$', msg="Trailing whitespace", **kwds)


def triple_colon(ticket, **kwds):
    """
    Look for the presence of triple colons `:::`.
    """
    exclude_new(ticket, regex=r'\:\:\:', msg="Triple colon (:::)", **kwds)


def trac_links(ticket, **kwds):
    """
    Look for the presence of badly formatted trac roles ``:trac:``,
    missing the initial colon.
    """
    exclude_new(ticket, regex=r'[^\:]trac\:`[0-9]', msg="Bad trac link", **kwds)


def non_ascii(ticket, **kwds):
    """
    Look for the presence of non-ascii characters in python and cython files.

    This should be done file by file to check for unicode declaration.
    """
    not_declared = lambda file: (not(check_unicode_declaration(file)) and
                                 file.split('.')[-1] in ['py', 'pyx'])
    exclude_new_file_by_file(ticket, regex=r'[^\x00-\x7F]',
                             file_condition=not_declared,
                             msg="Non-ascii characters", **kwds)


def check_unicode_declaration(file):
    """
    Check if the encoding is declared as utf-8 as in PEP0263.

    Return True if there is a correct utf-8 declaration.

    This is one example of the file condition that can be used
    in exclude_new_file_by_file.

    This one is useful in the `non_ascii` plugin.
    """
    regex = re.compile(r"coding[:=]\s*([-\w.]+)")
    f = open(file)
    L0 = regex.split(f.readline())
    L1 = regex.split(f.readline())
    f.close()
    if len(L0) >= 2 and L0[1] == 'utf-8':
        return True
    if len(L1) >= 2 and L1[1] == 'utf-8':
        return True
    return False


def input_output_block(ticket, **kwds):
    """
    no :: after INPUT and OUTPUT blocks
    """
    exclude_new(ticket, regex=r'^\s*[A-Z]*PUT\:\:',
                msg="Bad Input/Output blocks", **kwds)


def reference_block(ticket, **kwds):
    """
    no :: after REFERENCE blocks
    """
    exclude_new(ticket, regex=r'^\s*REFERENCES?\:\:',
                msg="Bad reference blocks", **kwds)


def doctest_continuation(ticket, **kwds):
    """
    Check that doctest continuation use syntax `....:`.
    """
    exclude_new(ticket, regex=r'^\s*\.\.\.\s',
                msg="Old-style doctest continuation", **kwds)


def raise_statements(ticket, **kwds):
    """
    Check that raise statements use python3 syntax.
    """
    exclude_new(ticket, regex=r'^\s*raise\s*[A-Za-z]*Error,',
                msg="Old-style raise statement", **kwds)


def commit_messages(ticket, patches, **kwds):
    """
    Check for the existence of a commit message for every commit.

    This is now for git only.
    """
    for patch_path in patches:
        patch = os.path.basename(patch_path)
        print("Looking at {}".format(patch))
        header = []
        for line in open(patch_path):
            if line.startswith('diff '):
                break
            header.append(line)
        else:
            print(''.join(header[:10]))
            raise ValueError("Not a valid patch file: " + patch)
        print(''.join(header))
    print("All patches good.")


def startup_modules(ticket, sage_binary, baseline=None, **kwds):
    """
    Count modules imported at startup.
    """
    # Sometimes the first run does something different...
    do_or_die(sage_binary + " -c ''")
    # Print out all the modules imported at startup.
    modules = subprocess.check_output([sage_binary, "-c", r"print '\n'.join(sorted(sys.modules.keys()))"]).split('\n')

    print("Total count: {}".format(len(modules)))
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
            print("New:")
            print("\n    ".join(new))
        else:
            status = PluginResult.Passed
        if removed:
            print("Removed:")
            print("\n    ".join(removed))
        data = {'new': new, 'removed': removed}

    if baseline:
        print("=" * 20)
    print('\n'.join(modules))
    return PluginResult(status, baseline=modules, data=data)


def startup_time(ticket, make, sage_binary, loops=5, total_samples=50,
                 dry_run=False, **kwds):
    """
    Try to decide if the startup time is getting worse.
    """
    if dry_run:
        loops //= 2
        total_samples //= 5

    print("{} samples in {} loops".format(total_samples, loops))
    ticket_id = ticket['id']
    choose_base = "git checkout patchbot/base; {} build > /dev/null".format(make)
    choose_ticket = "git checkout patchbot/ticket_merged; {} build  > /dev/null".format(make)

    def startup_times(samples):
        do_or_die(sage_binary + " -c ''")
        all = []
        for k in range(samples):
            start = time.time()
            do_or_die(sage_binary + " -c ''")
            all.append(time.time() - start)
        return all

    try:
        main_timings = []
        do_or_die(choose_base)
        for k in range(loops):
            main_timings.extend(startup_times(total_samples //
                                              loops + 2 * k - loops + 1))
        ticket_timings = []
        do_or_die(choose_ticket)
        for k in range(loops):
            ticket_timings.extend(startup_times(total_samples //
                                                loops + 2 * k - loops + 1))

        print("main_timings = {}".format(main_timings))
        print("ticket_timings = {}".format(ticket_timings))

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

        print("Main:   %0.5g sec (%s samples, std_dev=%0.3g)" % (p1, n1, s1))
        print("Ticket: %0.5g sec (%s samples, std_dev=%0.3g)" % (p2, n2, s2))
        print("Average %s of %0.2g secs or %0.2g%%." % (
              inc_or_dec[increased][:-1], diff, 100 * diff / base))
        print("Using the Mann-Whitney U test to determine significance.")

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
            print("No statistically significant difference.")
        else:
            print("May have caused a slowdown.")
        for confidence, lower_bound, in confidence_intervals:
            if increased and confidence >= .95 and lower_bound >= .001:
                status = PluginResult.Failed
            confidence = 1 - float(("%0.1g" if confidence > .9
                                    else "%0.2g") % (1 - confidence))
            print("With %g%% confidence, startup time %s by at least %0.2g%%" % (
                100 * confidence, inc_or_dec[increased], 100 * lower_bound))

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
            print("{} must be of the form --kwd=expr".format(arg))
            sys.exit(1)
        key = m.group(1)
        if m.group(2) == m.group(3):
            value = m.group(2)
        else:
            value = eval(m.group(2))
        kwds[key] = value
    plugin(**kwds)
