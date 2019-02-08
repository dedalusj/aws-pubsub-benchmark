#!/usr/bin/env python

import os

import boto3
import click
import invoke

_TEMPLATE_FILE = 'template.yaml'
_BUILD_DIR = '.aws-sam/build'
_BUILT_TEMPLATE_FILE = os.path.join(_BUILD_DIR, 'template.yaml')
_OUTPUT_TEMPLATE_FILE = os.path.join(_BUILD_DIR, 'packaged-template.yaml')
_STACK_NAME = 'pubsub-benchmark'


@click.group()
def cli():
    pass


@cli.command()
@click.option('--bucket', type=click.STRING, required=True, help='S3 bucket used to upload lambda artifacts')
def run(bucket):
    """
    Package, deploy and run the benchmark test
    """
    invoke.run('pipenv lock -r > functions/requirements.txt')
    invoke.run(f'sam build --template {_TEMPLATE_FILE} --build-dir {_BUILD_DIR}')
    invoke.run(f'sam package --template-file {_BUILT_TEMPLATE_FILE} \
        --output-template-file {_OUTPUT_TEMPLATE_FILE} --s3-bucket "{bucket}"')
    invoke.run(f'sam deploy --template-file {_OUTPUT_TEMPLATE_FILE} \
        --stack-name {_STACK_NAME} --capabilities CAPABILITY_IAM')


@cli.command()
def clean():
    """
    Clean up all resources associated with the benchmark
    """
    click.echo('Syncing')


if __name__ == '__main__':
    cli()
