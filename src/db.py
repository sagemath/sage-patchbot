# to launch a mongo console:
# mongod --port=21002

import gridfs
from pymongo.mongo_client import MongoClient
mongo_port = 21002

mongodb = MongoClient(port=mongo_port).buildbot
tickets = mongodb.tickets
tickets.ensure_index('id', unique=True)
tickets.ensure_index('status')
tickets.ensure_index('authors')
tickets.ensure_index('participants')
tickets.ensure_index('last_activity')
tickets.ensure_index('reports.base')
tickets.ensure_index('reports.machine')
tickets.ensure_index('reports.time')

logs = gridfs.GridFS(mongodb, 'logs')


def lookup_ticket(ticket_id):
    """
    Look up for a ticket in the database
    """
    return tickets.find_one({'id': ticket_id})


def save_ticket(ticket_data):
    """
    Save ticket data in the database
    """
    old = tickets.find_one({'id': ticket_data['id']})
    if old:
        old.update(ticket_data)
        ticket_data = old
    tickets.save(ticket_data)
