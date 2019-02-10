#!/usr/bin/env python

import datetime
import json
import logging
import multiprocessing
import os
import queue
import re
import time

import boto3
import click
import invoke
import pandas as pd

_TEMPLATE_FILE = 'template.yaml'
_BUILD_DIR = '.aws-sam/build'
_BUILT_TEMPLATE_FILE = os.path.join(_BUILD_DIR, 'template.yaml')
_OUTPUT_TEMPLATE_FILE = os.path.join(_BUILD_DIR, 'packaged-template.yaml')

_RATE_REGEX = r'^([0-9]+)\/([sm])$'
_DURATION_REGEX = r'^([0-9]+)([sm])$'

_QUEUE_SIZE = 200
_NUM_WORKERS = 20
_MAX_BATCH_SIZE = 5

_DEFAULT_STACK_NAME = 'pubsub-benchmark'
_DEFAULT_MEMORY = 512
_DEFAULT_SHARDS = 5
_DEFAULT_DURATION = 300.0
_DEFAULT_CALLS_PER_SECOND = 20.0
_DEFAULT_BENCHMARK_DISCARDED_PERCENT = 0.3

cloudformation_client = boto3.client('cloudformation')
xray_client = boto3.client('xray')


def setup_logging(debug):
    logging.basicConfig(format='%(asctime)s %(message)s', datefmt='%Y-%m-%d %H:%M:%S',
                        level=logging.DEBUG if debug else logging.INFO)
    logging.getLogger('boto3').setLevel(logging.CRITICAL)
    logging.getLogger('botocore').setLevel(logging.CRITICAL)
    logging.getLogger('requests').setLevel(logging.CRITICAL)
    logging.getLogger('urllib3').setLevel(logging.CRITICAL)


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


def deploy_lambda_functions(shards=_DEFAULT_SHARDS, memory=_DEFAULT_MEMORY, stack_name=_DEFAULT_STACK_NAME):
    logging.info(f'Deploying benchmarking stack with name [{stack_name}]')
    invoke.run(f'sam deploy --template-file {_OUTPUT_TEMPLATE_FILE} \
            --parameter-overrides Shards={shards} Memory={memory} \
            --stack-name {stack_name} --capabilities CAPABILITY_IAM')


def delete_stack(stack_name=_DEFAULT_STACK_NAME):
    logging.info(f'Deleting CloudFormation stack with name [{stack_name}]')
    invoke.run(f'aws cloudformation delete-stack --stack-name "{stack_name}"')


def wait_for_stack_deletion(stack_name=_DEFAULT_STACK_NAME):
    logging.info(f'Waiting for stack [{stack_name}] deletion')
    invoke.run(f'aws cloudformation wait stack-delete-complete --stack-name "{stack_name}"')


def get_function_name(stack_name=_DEFAULT_STACK_NAME):
    response = cloudformation_client.describe_stacks(StackName=stack_name)
    return response['Stacks'][0]['Outputs'][0]['OutputValue']


class Throttler(multiprocessing.Process):
    """
    Throttler submits messages to a queue at a given rate.
    """
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
    """
    A worker pull messages from a queue and invoke the fan out lambda function
    defined in the template at every message. It will block if the queue is empty.
    """
    def __init__(self, worker_id, rate_queue, function_name):
        super().__init__()
        self.exit = multiprocessing.Event()
        self.id = worker_id
        self.rate_queue = rate_queue
        self.function_name = function_name
        self.invocations = 0

    def run(self):
        lambda_client = boto3.client('lambda')
        while not self.exit.is_set():
            try:
                self.rate_queue.get(timeout=0.01)
                logging.debug(f'Worker [{self.id}] invoking lambda')
                lambda_client.invoke(FunctionName=self.function_name, InvocationType='Event')
                self.invocations += 1
            except queue.Empty:
                continue

    def shutdown(self):
        logging.debug(f'Worker [{self.id}] shutting down')
        self.exit.set()


def run_benchmark(rate=_DEFAULT_CALLS_PER_SECOND, duration=_DEFAULT_DURATION, stack_name=_DEFAULT_STACK_NAME):
    """
    Run the benchmark by invoking the fan out lambda function
    at the given rate until the duration time of the benchmark is reached.
    """
    function_name = get_function_name(stack_name=stack_name)
    logging.info(f'Submitting messages with rate [{rate}] for [{duration}]')
    rate, duration = parse_rate(rate), parse_duration(duration)
    rate_queue = multiprocessing.Manager().Queue(maxsize=_QUEUE_SIZE)

    throttler = Throttler(rate_queue, 1.0 / rate)
    workers = [Worker(str(i), rate_queue, function_name) for i in range(_NUM_WORKERS)]

    # start throttler and workers
    throttler.start()
    for p in workers:
        p.start()

    # wait for the required duration
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

    total_invocations = sum([w.invocations for w in workers])
    logging.info(f'Run {total_invocations} in {duration} seconds')


def get_traces_time_interval(duration=_DEFAULT_DURATION, discarded=_DEFAULT_BENCHMARK_DISCARDED_PERCENT):
    """
    Compute the start and end time used to fetch all X-Ray traces.
    The discarded parameter is used to exclude the initial period of the
    benchmark from the time interval under interest. This is to avoid Lambda
    cold start from affecting the benchmark measurements.
    """
    start_time = datetime.datetime.utcnow() - datetime.timedelta(seconds=duration * (1.0 - discarded))
    end_time = datetime.datetime.utcnow()
    return start_time, end_time


def get_trace_ids(start_time, end_time):
    """Fetch the ids of all the X-Ray traces between start time and end time"""
    trace_ids = []
    for p in xray_client.get_paginator('get_trace_summaries')\
            .paginate(StartTime=start_time, EndTime=end_time, FilterExpression='annotation.scope = "benchmark"'):
        trace_ids.extend([t['Id'] for t in p['TraceSummaries']])
    return trace_ids


def chunks(l, n):
    """Divide the list l into chunks of at most n elements"""
    for i in range(0, len(l), n):
        yield l[i:i + n]


def get_segments(trace_ids):
    """
    Fetch the complete segments information for the given trace IDs filtering
    the ones containing benchmark in their name as the relevant ones for this application.
    """
    def compute_duration(segment):
        segment['duration'] = segment['end_time'] - segment['start_time']
        return segment

    def unpack_annotations(segment):
        segment.update(segment['annotations'])
        segment.pop('annotations')
        return segment

    segments = {}
    for ids in chunks(trace_ids, _MAX_BATCH_SIZE):
        traces = xray_client.batch_get_traces(TraceIds=ids)['Traces']
        segments.update({t['Id'] + s['Id']: s for t in traces for s in t['Segments']})
    segments = [json.loads(v['Document']) for v in segments.values()]
    segments = [v for v in segments if '_benchmark' in v['name']]
    segments = [compute_duration(s) for s in segments]
    segments = [unpack_annotations(s) for s in segments]
    return segments


def get_dataframe(segments):
    """
    Create a pandas dataframe from a list of segments
    filtering out all segments with a negative duration
    """
    df = pd.DataFrame(segments)
    return df[df['duration'] > 0]


def compute_service_stats(df):
    """Compute relevant latency statistics by pub/sub system"""
    def compute(group):
        return {'count': int(group.count()), 'mean': group.mean(), 'p50': group.median(),
                'p75': group.quantile(q=0.75), 'p90': group.quantile(q=0.90), 'p95': group.quantile(q=0.95)}

    stats = df['duration'].groupby(df['service']).apply(compute).unstack()
    stats.loc[:, stats.columns != 'count'] = stats.loc[:, stats.columns != 'count'] * 1000.0
    stats['count'] = stats['count'].astype('int')
    return stats


def get_stats(duration=_DEFAULT_DURATION, discarded=_DEFAULT_BENCHMARK_DISCARDED_PERCENT):
    """
    Get the data and compute the latency statistics for the
    various pub/sub systems after the benchmark has run
    """
    logging.info(f'Gathering benchmark results')
    duration = parse_duration(duration)
    start_time, end_time = get_traces_time_interval(duration=duration, discarded=discarded)
    trace_ids = get_trace_ids(start_time, end_time)
    segments = get_segments(trace_ids)
    df = get_dataframe(segments)
    return compute_service_stats(df)


@click.command()
@click.option('--bucket', type=click.STRING, required=True, help='S3 bucket used to upload lambda artifacts')
@click.option('--shards', type=click.IntRange(min=1, max=100), default=_DEFAULT_SHARDS, show_default=True,
              help='Number of Kinesis shards to use')
@click.option('--memory', type=click.Choice(['128', '256', '512', '1024', '2048']), default=str(_DEFAULT_MEMORY),
              show_default=True, help='Memory allocated for the lambda functions', callback=validate_memory)
@click.option('--stack-name', type=click.STRING, default=_DEFAULT_STACK_NAME,
              show_default=True, help='Named of the cloudformation stack created for the benchmark')
@click.option('--rate', type=click.STRING, default=f'{int(_DEFAULT_CALLS_PER_SECOND)}/s', show_default=True,
              callback=validate_rate, help='Rate of messages delivered to the pub sub queues')
@click.option('--duration', type=click.STRING, default=f'{int(_DEFAULT_DURATION)}s', show_default=True,
              callback=validate_duration, help='Duration of the benchmark')
@click.option('--discarded', type=click.FLOAT, default=_DEFAULT_BENCHMARK_DISCARDED_PERCENT, show_default=True,
              help='Percentage, between 0 and 1, of traces to be discarded from the beginning of the benchmark')
@click.option('--debug/--no-debug', default=False)
def cli(bucket, shards, memory, stack_name, rate, duration, discarded, debug):
    """
    Run a benchmark measuring the latency of various pub/sub and messaging systems in AWS.

    It will deploy a CloudFormation stack with various lambda functions and will repeatedly call
    a special lambda function to propagate messages using SNS, SQS, DynamoDB and Kinesis. It will
    record the delivery time of such messages using X-Ray and provide latency statistics for
    this services. It will finally destroy the CloudFormation stack leaving no dangling resources.
    """
    setup_logging(debug)
    create_requirements_file()
    build_lambda_functions()
    package_lambda_function(bucket)
    deploy_lambda_functions(shards=shards, memory=memory, stack_name=stack_name)

    run_benchmark(rate, duration=duration, stack_name=stack_name)
    stats = get_stats(duration=duration, discarded=discarded)
    logging.info(f'Latency statistics -- AWS Lambda memory [{memory}] -- Kinesis shards [{shards}]')
    print(stats)

    delete_stack(stack_name=stack_name)
    wait_for_stack_deletion(stack_name=stack_name)


if __name__ == '__main__':
    cli()
