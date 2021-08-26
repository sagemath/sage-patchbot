"""
a bunch of tools for maintenance of the patchbot server

to be used in an ipython session for the user ``patchbot``

.. WARNING:: Use with caution!
"""
from __future__ import annotations
from sage_patchbot.server.db import tickets, logs


def get_tickets_with_many_reports(N: int) -> list[int]:
    """
    Retrieve the tickets with more than N reports.

    INPUT: N an integer

    OUTPUT: list of ticket numbers
    """
    return [t['id'] for t in tickets.find()
            if 'reports' in t and len(t['reports']) > N]


def purge_tickets_with_many_reports(N: int, n: int):
    """
    For all tickets with more than N reports, keep only the latest n reports.

    INPUT: integers N, n

    .. WARNING:: Use with caution!
    """
    assert n < N
    longs = get_tickets_with_many_reports(N)
    for fi in longs:
        old = tickets.find_one({'id': fi})['reports']
        tickets.update_one({'id': fi}, {'$set': {"reports": old[-n:]}})


def get_pending_logs(year: int):
    """
    Retrieve an iterator over ``Pending`` logs for the given ``year``.

    INPUT: an integer, for example 2019

    OUTPUT: an iterator over database entries
    """
    return logs.find({'_id': {'$regex': f"/log/Pending/.*/{year}"}})


def count_pending_logs(year: int) -> int:
    """
    Count the number of ``Pending`` logs for the given ``year``.

    INPUT: an integer, for example 2019

    OUTPUT: an integer
    """
    logs_year = get_pending_logs(year)
    return logs_year.count()


def purge_pending_logs(year: int):
    """
    Delete all ``Pending`` logs for the given ``year``.

    INPUT: an integer, for example 2019

    .. WARNING:: Use with caution!
    """
    year_logs = get_pending_logs(year)
    for ell in year_logs:
        logs.delete(ell._file['_id'])


def purge_pending_in_tickets(liste: list[int]):
    """
    Delete all ``Pending`` logs for all given tickets.

    INPUT: a list of trac ticket numbers, such as [8954, 22453]

    .. WARNING:: Use with caution!
    """
    for l in liste:
        pending_logs = logs.find({'_id': {'$regex': f"/log/Pending/{l}/"}})
        for ell in pending_logs:
            logs.delete(ell._file['_id'])


def count_logs(year: int, month: int, day=0) -> int:
    """
    Return the numbers of logs for a given period.

    INPUT: year and month as numbers, such as 2019, 3

    optionally also the day as a number

    OUTPUT: integer
    """
    if not day:
        reg = f"/log/.*/{year}-{month:02d}.*"
    else:
        reg = f"/log/.*/{year}-{month:02d}-{day:02d}.*"
    period_logs = logs.find({'_id': {'$regex': reg}})
    return period_logs.count()


def extraction_machine(list_of_logs: list) -> list[str]:
    """
    Extract, from a list of database entries, the full names
    of the machines that sent these reports.

    INPUT: a list or iterator of some ``logs`` database entries

    OUTPUT: a sorted list of short machine names

    In [11]: h = get_pending_logs(2021)
    In [12]: extraction_machine(h)
    Out[12]: ['panke', 'pc72-math', 'petitbonum']
    """
    file_names = [g._file['_id'].split('/') for g in list_of_logs]
    file_names = [[txt for txt in f if txt != 'Pending']
                  for f in file_names]
    return sorted(set(f[-2] for f in file_names))


def machines_actives(year: int, month: int) -> list[str]:
    """
    Return the list of machines that were active during the period.

    INPUT: integers for year and month

    OUTPUT: list of short machine names

    In [13]: machines_actives(2021, 8)
    Out[13]:
    ['convex63',
    'panke',
    'pascaline',
    'pc72-math',
    'petitbonum',
    'tmonteil-lxc1',
    'tmonteil-lxc2',
    'tmonteil-lxc3',
    'tmonteil-lxc4']
    """
    bads = logs.find({'_id': {'$regex': f"/log/.*/{year}-{month:02d}.*"}})
    return extraction_machine(bads)
