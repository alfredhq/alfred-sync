from .sync import SyncHandler


def run_worker(task, config):
    SyncHandler.run(config['database_uri'], task['user_id'])
