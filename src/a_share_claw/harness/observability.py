"""Public decision/action/observation spans owned by the execution code."""
from __future__ import annotations

import asyncio
import re
import time
from uuid import uuid4

from .contracts import digest, redact

KINDS = {'source', 'model', 'role', 'evaluation', 'artifact', 'planning', 'evidence', 'calculation'}


class ActionSpan:
    def __init__(self, session, kind, operation, decision, inputs, *, parent_id=...):
        if kind not in KINDS or not all(isinstance(s,str) and s.strip() for s in (operation,decision)):
            raise ValueError('invalid_action_span')
        self.session, self.kind, self.operation = session, kind, operation
        self.explicit_parent_id=parent_id
        self.decision, self.inputs = decision, inputs
        self.span_id, self.observation, self.status = uuid4().hex, {}, 'ok'
        self.ended = False

    def __enter__(self):
        session = self.session
        with session.action_lock:
            self.parent_id = next(reversed(session.open_actions), None) if self.explicit_parent_id is ... else self.explicit_parent_id
            if self.parent_id is not None and self.parent_id not in session.open_actions:raise ValueError("invalid_action_parent")
            self.started = time.monotonic()
            self.phase = session.active_phase
            session.step('react_action', {'schema_version':'react-action-v1', 'span_id':self.span_id,
                'parent_span_id':self.parent_id, 'phase':self.phase, 'kind':self.kind, 'operation':self.operation,
                'boundary':'start', 'decision_code':self.decision, 'reasoning_kind':'public_decision_summary',
                'input_hash':digest(redact(self.inputs))}, 'running')
            session.open_actions[self.span_id] = self
        return self

    def observe(self, *, status='ok', **observation):
        if status not in {'ok','rejected','denied','error','cancelled','timeout'}:
            raise ValueError('invalid_action_status')
        self.status, self.observation = status, redact(observation)

    def end_detail(self, status=None):
        return {'schema_version':'react-action-v1', 'span_id':self.span_id, 'parent_span_id':self.parent_id,
            'phase':self.phase, 'kind':self.kind, 'operation':self.operation, 'boundary':'end',
            'duration_ms':round((time.monotonic()-self.started)*1000,3),
            'observation':{} if status == 'interrupted' else self.observation}

    def __exit__(self, exc_type, exc, traceback):
        with self.session.action_lock:
            if self.ended or self.session.closed:
                return False  # A late callback cannot modify the terminal run.
            status = self.status
            if exc is not None:
                status = 'cancelled' if isinstance(exc,asyncio.CancelledError) else 'timeout' if isinstance(exc,TimeoutError) else 'error'
                # Domain codes are useful; arbitrary exception text can contain secrets/prose.
                code = str(exc) if isinstance(exc,ValueError) and re.fullmatch(r'[A-Za-z0-9_:.-]{1,120}',str(exc)) else None
                self.observation = {'exception_type':exc_type.__name__, 'error_code':code}
            if self.session.control.cancelled.is_set() and status == 'ok':
                status, self.observation = 'cancelled', {}
            self.session.step('react_action',self.end_detail(),status)
            self.session.open_actions.pop(self.span_id,None)
            self.ended = True
        return False
