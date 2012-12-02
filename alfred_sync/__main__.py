import argparse
import logging
import signal
import yaml

from functools import partial
from .process import SyncProcess


def get_config(path):
    with open(path) as file:
        return yaml.load(file)


def terminate_processes(processes, signum, frame):
    for process in processes:
        if process is not None and process.is_alive():
            process.terminate()
            process.join()


def main():
    logging.basicConfig(format='%(levelname)s:%(message)s', level=logging.INFO)
    parser = argparse.ArgumentParser()
    parser.add_argument('config')
    args = parser.parse_args()
    config = get_config(args.config)

    processes = []
    for i in range(config['num_workers']):
        process = SyncProcess(config)
        process.start()
        processes.append(process)

    signal.signal(signal.SIGTERM, partial(terminate_processes, processes))

    for process in processes:
        process.join()


if __name__ == '__main__':
    main()
