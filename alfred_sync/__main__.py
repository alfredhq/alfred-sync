import argparse
import multiprocessing
import msgpack
import yaml
import zmq

from .worker import run_worker


def get_config(path):
    with open(path) as file:
        return yaml.load(file)


def run(config):
    context = zmq.Context()
    socket = context.socket(zmq.PULL)
    socket.bind(config['sync'])

    pool = multiprocessing.Pool(processes=config.get('num_workers'))
    try:
        while True:
            msg = socket.recv()
            task = msgpack.unpackb(msg, encoding='utf-8')
            pool.apply_async(run_worker, args=(task, config))
    except KeyboardInterrupt:
        pass
    finally:
        pool.terminate()

    socket.close()
    context.term()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('config')
    args = parser.parse_args()

    config = get_config(args.config)
    run(config)


if __name__ == '__main__':
    main()
