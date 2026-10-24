import threading

from eidos_runtime.db.maintenance import StorageMaintenance


def test_storage_maintenance_keeps_cleanup_without_model_or_job_dependencies():
    cancel = threading.Event()

    class Memory:
        def cleanup(self):
            cancel.set()

    StorageMaintenance(Memory(), lambda: False, lambda _: None).run(cancel)
    assert cancel.is_set()
