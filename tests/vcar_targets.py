"""Child-process targets for the V-CAR bounded-runner tests.

These run in a separate interpreter (the task-budget child), so they only use stdlib.

The single argument is the V-CAR wrapper payload, not the raw node input:
``{'node_id', 'tool', 'input', 'expect', 'inputs'}`` where ``input`` is the node's own
structured input and ``inputs`` maps dependency node ids to their results.
"""
import os
import time


def echo(payload):
    return {'node_id': payload['node_id'], 'tool': payload['tool'], 'pid': os.getpid(),
            'input': payload['input'], 'inputs': payload['inputs']}


def sleep_forever(payload):
    time.sleep(payload['input'].get('seconds', 300))
    return {'ok': True}


def mark_and_sleep(payload):
    with open(payload['input']['marker'], 'w') as handle:
        handle.write(str(os.getpid()))
    time.sleep(payload['input'].get('seconds', 300))
    return {'ok': True}


def rows(payload):
    return {'rows': payload['input'].get('rows', 0), 'tokens': payload['input'].get('tokens', 0)}
