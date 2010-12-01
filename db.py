import os

# mongod --port=21000 --dbpath=data
import pymongo, gridfs
from pymongo import Connection
mongo_port = 21000

mongodb = Connection(port=mongo_port).buildbot
tickets = mongodb.tickets
tickets.ensure_index('id')
tickets.ensure_index('status')
tickets.ensure_index('authors')
tickets.ensure_index('participants')
tickets.ensure_index('reports.base')
tickets.ensure_index('reports.machine')

logs = gridfs.GridFS(mongodb, 'logs')

def lookup_ticket(ticket_id):
    return tickets.find_one({'id': ticket_id})

def save_ticket(ticket_data):
    old = lookup_ticket(ticket_data['id'])
    if old:
        old.update(ticket_data)
        ticket_data = old
    tickets.save(ticket_data)

def current_reports(ticket, base=None):
    if 'reports' not in ticket:
        return []
    return filter(lambda report: (ticket['patches'] == report['patches'] and
                                  ticket['spkgs'] == report['spkgs'] and
                                  (base is None or base == report['base'])),
                  ticket['reports'])
