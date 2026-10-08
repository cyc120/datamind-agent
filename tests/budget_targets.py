"""Child-process targets for the task-budget resource-limit tests."""


def echo(payload):
    return {'ok': True, 'payload': payload}


def spin(payload):
    """Burn CPU until the CPU rlimit kills this process."""
    import time
    deadline = time.monotonic() + payload.get('seconds', 300)
    loops = 0
    while time.monotonic() < deadline:
        loops += 1
    return {'ok': True, 'loops': loops}


def allocate(payload):
    """Allocate until the address-space rlimit raises MemoryError."""
    blocks = []
    for _ in range(payload.get('blocks', 64)):
        blocks.append(bytearray(payload.get('block_bytes', 64 * 1024 * 1024)))
    return {'ok': True, 'blocks': len(blocks)}
