# Pub/Sub and Messaging Benchmark

Benchmark for various pub/sub and messaging brokers in AWS. 

It uses the [AWS SAM Cli](https://github.com/awslabs/aws-sam-cli) to deploy a series of AWS Lambda functions to distribute and record the delivery time of messages across services like SNS, SQS, DynamoDB and Kinesis.

## Pre-requisites

### pipenv

All the dependencies necessary to deploy and run the tool are managed with [pipenv](https://github.com/pypa/pipenv). Please install it on your system and make sure it's available in your path.

Once pipenv has been installed you can install the dependencies for the project with the command `pipenv install --dev`.

### S3 bucket

Create an S3 bucket where the zip files containing the AWS Lambda functions will be uploaded.

## Running

The `benhcmark.py` is the entry point for running the benchmark. This can be done with the command `pipenv run benchmark --bucket <s3_bucket_name>`. This command will create the CloudFormation stack, invoke the necessary lambda functions, compute and print the statistics and finally destroy the stack to leave a clean environment.

Several options are available to tune the parameters of the benchmark, use `pipenv run benchmark --help` to discover them all.
