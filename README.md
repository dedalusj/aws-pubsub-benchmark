# Pub/Sub and Messaging Benchmark

Benchmark for various pub/sub and messaging brokers in AWS. It uses the [AWS SAM Cli](https://github.com/awslabs/aws-sam-cli) to deplyo a series of AWS Lambda functions for the timing as well as provisioning the various AWS oub/sub and messaging resources.

Finally it uses [k6](https://k6.io/) to execute multiple requests to the AWS Lambda submitting messages to the the pub/sub and messaging systems.

## Pre-requisites

### Docker

Th K6 tool executing the HTTP requests runs in docker so make sure docker is installed on your machine and available for your user.

### pipenv

All the dependencies necessary to deploy and run the tool are managed with [pipenv](https://github.com/pypa/pipenv). Please install it on your system and make sure it's available in your path.

### S3 bucket

Create an S3 bucket where the zip files containing the AWS Lambda functions will be uploaded.

## Running

The `benchmark.sh` bash script is the entry point to run the benchmark and clean the resources afterwards. There are two commands:

* `./benchmark.sh run <bucket_name>`. The run command will create the required AWS resources and run the benchmark. Bucket name is the name of the S3 bucket created as part of the pre-requisites. The results will be available in AWS X-Ray. In particular the latency distribution for every pub/sub and messaging system will be shown as `<system>_delivery` as leaf nodes of the `pubsub-benchmark-EntryFunction` in the X-Ray service map. By clicking on the various `<system>_delivery` you can see the latency distribution for each service.
* `./benchmark.sh clean`. The clean command will delete the AWS CloudFormation stack creqted as part of the run command leabving no dangling AWS resources.
