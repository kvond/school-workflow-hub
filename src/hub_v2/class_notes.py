"""Persistent teacher notes per configured Agenda and class, with CAS/history.

These notes are independent of lesson Forms, dates and weeks. Passive reads
never create notes; notes do not authorize background work.
"""
import re

from school_workflow_hub.adapters.firestore_state import configured_store
from school_workflow_hub.core.cloud_state import Conflict, StoreUnavailable, key
from school_workflow_hub.core.runs import content_hash

SCHEMA = 'hub-class-notes/v1'
MAX_TEXT = 30000
CLASSES = {'biology', 'forensics', 'anatomy'}


def _id(config, class_key):
    if not isinstance(class_key, str) or class_key not in CLASSES:
        raise ValueError('Invalid class.')
    agenda = config.get('HUB_AGENDA_SPREADSHEET_ID', '')
    if not isinstance(agenda, str) or not re.fullmatch(r'[A-Za-z0-9_-]{10,200}', agenda):
        raise StoreUnavailable('Class notes need the configured Agenda.')
    return key('classnotes', {'agenda': agenda, 'class': class_key})


def _view(doc):
    if doc is None:
        return {'text': '', 'revision': '', 'updated_at': None}
    value = doc.value
    if (value.get('schema') != SCHEMA or not isinstance(value.get('text'), str)
            or not isinstance(value.get('updated_at'), str)):
        raise StoreUnavailable('Class notes could not be verified.')
    return {'text': value['text'], 'revision': content_hash(value), 'updated_at': value['updated_at']}


def read(config, class_key):
    ident = _id(config, class_key)
    return _view(configured_store(config).read([ident]).documents[ident])


def save(config, class_key, text, revision, *, actor):
    if (not isinstance(text, str) or len(text) > MAX_TEXT or '\x00' in text
            or not isinstance(revision, str) or not re.fullmatch(r'(?:[a-f0-9]{64})?', revision)
            or not isinstance(actor, str) or not actor):
        raise ValueError('Invalid notes.')
    ident, port = _id(config, class_key), configured_store(config)
    snapshot = port.read([ident])
    old = snapshot.documents[ident]
    current = _view(old)
    if current['revision'] != revision:
        raise Conflict('Class notes changed elsewhere. Compare the saved notes before saving your draft.')
    if current['text'] == text:
        return current
    value = {'schema': SCHEMA, 'scope': ident, 'text': text, 'updated_at': snapshot.read_time,
             'updated_by': actor, 'previous_revision': revision}
    history_id = key('classnoteshistory', [ident, content_hash(value)])
    try:
        port.commit({ident: (value, old.version if old else None), history_id: (value, None)})
    except Conflict:
        raise Conflict('Class notes changed elsewhere. Reload saved notes before saving your draft.') from None
    verified = port.read([ident, history_id]).documents
    if any(verified[k] is None or verified[k].value != value for k in (ident, history_id)):
        raise StoreUnavailable('Class notes save is unconfirmed. Reload saved notes before retrying.')
    return _view(verified[ident])
