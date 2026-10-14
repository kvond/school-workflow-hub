"""Teacher operational notes in the existing private store, with CAS and history.

Reading notes is passive. The text is never an instruction to a background
worker by itself; the worker must apply its separately authorized task scope.
"""
import re

from school_workflow_hub.adapters.firestore_state import configured_store
from school_workflow_hub.core.cloud_state import Conflict, StoreUnavailable, key
from school_workflow_hub.core.runs import content_hash

SCHEMA = 'hub-system-notes/v1'
MAX_TEXT = 30000


def _id(config):
    agenda = config.get('HUB_AGENDA_SPREADSHEET_ID', '')
    if not isinstance(agenda, str) or not re.fullmatch(r'[A-Za-z0-9_-]{10,200}', agenda):
        raise StoreUnavailable('System notes need the configured Agenda.')
    return key('systemnotes', agenda)


def _view(doc):
    if doc is None:
        return {'text': '', 'revision': '', 'updated_at': None}
    value = doc.value
    if (value.get('schema') != SCHEMA or not isinstance(value.get('text'), str)
            or not isinstance(value.get('updated_at'), str)):
        raise StoreUnavailable('System notes could not be verified.')
    return {'text': value['text'], 'revision': content_hash(value), 'updated_at': value['updated_at']}


def read(config):
    ident = _id(config)
    return _view(configured_store(config).read([ident]).documents[ident])


def save(config, text, revision, *, actor):
    if (not isinstance(text, str) or len(text) > MAX_TEXT or '\x00' in text
            or not isinstance(revision, str) or not re.fullmatch(r'(?:[a-f0-9]{64})?', revision)
            or not isinstance(actor, str) or not actor):
        raise ValueError('Invalid notes.')
    ident, port = _id(config), configured_store(config)
    snapshot = port.read([ident])
    old = snapshot.documents[ident]
    current = _view(old)
    if current['revision'] != revision:
        raise Conflict('Notes changed in another tab. Your draft is still here; compare the saved notes before saving again.')
    if current['text'] == text:
        return current
    value = {'schema': SCHEMA, 'text': text, 'updated_at': snapshot.read_time,
             'updated_by': actor, 'previous_revision': revision}
    history_id = key('systemnoteshistory', [ident, content_hash(value)])
    try:
        port.commit({ident: (value, old.version if old else None), history_id: (value, None)})
    except Conflict:
        raise Conflict('Notes changed in another tab. Your draft is still here; reload saved notes before saving again.') from None
    verified = port.read([ident, history_id]).documents
    if any(verified[k] is None or verified[k].value != value for k in (ident, history_id)):
        raise StoreUnavailable('Notes save is unconfirmed. Your draft is still here; reload saved notes before retrying.')
    return _view(verified[ident])
