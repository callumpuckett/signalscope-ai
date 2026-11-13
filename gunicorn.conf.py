"""Keep scheduler threads and their health state in the serving worker, including preload."""


def post_worker_init(worker):
    from app import start_runtime_schedulers

    start_runtime_schedulers()
