from collections.abc import Iterable

from eidos_runtime.memory.repository import MemoryRejected


def context_epochs(context: Iterable[dict[str, object]]) -> dict[str, int]:
    epochs: dict[str, int] = {}
    for item in context:
        values = item.get("memoryEpochs")
        if not isinstance(values, dict):
            continue
        for scope, epoch in values.items():
            if not isinstance(scope, str) or type(epoch) is not int or epoch < 0:
                raise MemoryRejected("memory_snapshot_invalid")
            if scope in epochs and epochs[scope] != epoch:
                raise MemoryRejected("memory_snapshot_revoked")
            epochs[scope] = epoch
    return epochs
