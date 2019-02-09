#!/usr/bin/env python

import logging
import multiprocessing
import os
import queue
import re
import time

import boto3
import click
import invoke

_TEMPLATE_FILE = 'template.yaml'
_BUILD_DIR = '.aws-sam/build'
_BUILT_TEMPLATE_FILE = os.path.join(_BUILD_DIR, 'template.yaml')
_OUTPUT_TEMPLATE_FILE = os.path.join(_BUILD_DIR, 'packaged-template.yaml')
_STACK_NAME = 'pubsub-benchmark'
_RATE_REGEX = r'^([0-9]+)\/([sm])$'
_DURATION_REGEX = r'^([0-9]+)([sm])$'
_QUEUE_SIZE = 200
_NUM_WORKERS = 20

cloudformation_client = boto3.client('cloudformation')


def validate_rate(ctx, param, value):
    if re.match(_RATE_REGEX, value) is None:
        raise click.BadParameter('rate need to be in format like 30/s')
    return value


def validate_duration(ctx, param, value):
    if re.match(_DURATION_REGEX, value) is None:
        raise click.BadParameter('duration need to be in format like 30s')
    return value


def validate_memory(ctx, param, value):
    return int(value)


def parse_rate(rate):
    m = re.match(_RATE_REGEX, rate)
    num, unit = float(m.group(1)), m.group(2)
    return num / 60.0 if unit == 'm' else num


def parse_duration(duration):
    m = re.match(_DURATION_REGEX, duration)
    num, unit = float(m.group(1)), m.group(2)
    return num * 60.0 if unit == 'm' else num


def create_requirements_file():
    logging.info('Creating functions requirements file')
    invoke.run('pipenv lock -r > functions/requirements.txt')


def build_lambda_functions():
    logging.info('Building lambda function')
    invoke.run(f'sam build --template {_TEMPLATE_FILE} --build-dir {_BUILD_DIR}')


def package_lambda_function(bucket):
    logging.info(f'Packaging lambda function using S3 bucket [{bucket}]')
    invoke.run(f'sam package --template-file {_BUILT_TEMPLATE_FILE} \
            --output-template-file {_OUTPUT_TEMPLATE_FILE} --s3-bucket "{bucket}"')


def deploy_lambda_functions(shards, memory):
    logging.info(f'Deploying benchmarking stack with name [{_STACK_NAME}]')
    invoke.run(f'sam deploy --template-file {_OUTPUT_TEMPLATE_FILE} \
            --parameter-overrides Shards={shards} Memory={memory} \
            --stack-name {_STACK_NAME} --capabilities CAPABILITY_IAM')


def delete_stack():
    logging.info(f'Deleting CloudFormation stack with name [{_STACK_NAME}]')
    invoke.run(f'aws cloudformation delete-stack --stack-name "{_STACK_NAME}"')


def wait_for_stack_deletion():
    logging.info(f'Waiting for stack [{_STACK_NAME}] deletion')
    invoke.run(f'aws cloudformation wait stack-delete-complete --stack-name "{_STACK_NAME}"')


def get_function_name(stack_name=_STACK_NAME):
    response = cloudformation_client.describe_stacks(StackName=stack_name)
    return response['Stacks'][0]['Outputs'][0]['OutputValue']


class Throttler(multiprocessing.Process):
    def __init__(self, rate_queue, interval):
        super().__init__()
        self.exit = multiprocessing.Event()
        self.rate_queue = rate_queue
        self.interval = interval

    def run(self):
        try:
            while not self.exit.is_set():
                if not self.rate_queue.full():  # avoid blocking
                    self.rate_queue.put(0)
                time.sleep(self.interval)
        except BrokenPipeError:
            # main process is done
            return

    def shutdown(self):
        logging.debug(f'Throttler shutting down')
        self.exit.set()


class Worker(multiprocessing.Process):
    def __init__(self, worker_id, rate_queue, function_name):
        super().__init__()
        self.exit = multiprocessing.Event()
        self.id = worker_id
        self.rate_queue = rate_queue
        self.function_name = function_name

    def run(self):
        lambda_client = boto3.client('lambda')
        while not self.exit.is_set():
            try:
                self.rate_queue.get(timeout=0.01)
                logging.debug(f'Worker [{self.id}] invoking lambda')
                lambda_client.invoke(FunctionName=self.function_name, InvocationType='Event')
            except queue.Empty:
                continue

    def shutdown(self):
        logging.debug(f'Worker [{self.id}] shutting down')
        self.exit.set()


def run_benchmark(rate, duration):
    function_name = get_function_name()
    logging.info(f'Submitting messages with rate [{rate}] for [{duration}]')
    rate, duration = parse_rate(rate), parse_duration(duration)
    rate_queue = multiprocessing.Manager().Queue(maxsize=_QUEUE_SIZE)

    throttler = Throttler(rate_queue, 1.0 / rate)
    workers = [Worker(str(i), rate_queue, function_name) for i in range(_NUM_WORKERS)]

    # start throttler and workers
    throttler.start()
    for p in workers:
        p.start()

    start_time = time.time()
    while (time.time() - start_time) <= duration:
        time.sleep(1)
        logging.info(f'Elapsed time {time.time() - start_time}')

    # stop workers and wait for them to finish
    for w in workers:
        w.shutdown()
    for w in workers:
        w.join()
    throttler.shutdown()


@click.group()
@click.option('--debug/--no-debug', default=False)
def cli(debug):
    logging.basicConfig(format='%(asctime)s %(message)s', datefmt='%Y-%m-%d %H:%M:%S',
                        level=logging.DEBUG if debug else logging.INFO)
    logging.getLogger('boto3').setLevel(logging.CRITICAL)
    logging.getLogger('botocore').setLevel(logging.CRITICAL)
    logging.getLogger('requests').setLevel(logging.CRITICAL)
    logging.getLogger('urllib3').setLevel(logging.CRITICAL)


@cli.command()
@click.option('--bucket', type=click.STRING, required=True, help='S3 bucket used to upload lambda artifacts')
@click.option('--shards', type=click.IntRange(min=1, max=100), default=50, show_default=True,
              help='Number of Kinesis shards to use')
@click.option('--memory', type=click.Choice(['128', '256', '512', '1024', '2048']), default='1024',
              show_default=True, help='Memory allocated for the lambda functions', callback=validate_memory)
@click.option('--rate', type=click.STRING, default='20/s', show_default=True, callback=validate_rate,
              help='Rate of messages delivered to the pub sub queues')
@click.option('--duration', type=click.STRING, default='300s', show_default=True, callback=validate_duration,
              help='Duration of the benchmark')
def run(bucket, shards, memory, rate, duration):
    """
    Package, deploy and run the benchmark test
    """
    create_requirements_file()
    build_lambda_functions()
    package_lambda_function(bucket)
    deploy_lambda_functions(shards, memory)
    run_benchmark(rate, duration)


@cli.command()
def clean():
    """
    Clean up all resources associated with the benchmark
    """
    delete_stack()
    wait_for_stack_deletion()


if __name__ == '__main__':
    cli()
