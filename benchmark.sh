#!/usr/bin/env bash

set -euf -o pipefail

info() {
    echo "INFO: $*" 1>&2
}

die() {
    local code=$?
    echo "ERROR: $*" 1>&2
    exit ${code}
}

PROG_NAME=$(basename $0)
OUTPUT_FILE=".sam_output.yml"
STACK_NAME="pubsub-benchmark"

pipenv --version > /dev/null 2>&1 || die "Please install pipenv"
  
sub_help(){
    echo "Usage: $PROG_NAME <subcommand> [options]\n"
    echo "Subcommands:"
    echo "    run   Create AWS resources and run the benchmark"
    echo "    clean Clean AWS resources previously created for the benchmark"
}
  
sub_run(){
    [ "$#" -ge 1 ] || die "Missing bucket name. Please use the run command as: $PROG_NAME run <bucket_name>"
    local S3_BUCKET="$1"

    info "Installing python dependencies"
    pipenv install --dev || die "Failed to install dependencies"

    info "Creating functions requirements file"
    pipenv lock -r > functions/requirements.txt || die "Failed to create requirement file"

    info "Using [${S3_BUCKET}] S3 bucket to store lambda functions"
    info "Building application dependencies"
    pipenv run sam build \
        --template template.yaml \
        --build-dir .aws-sam/build \
        || die "Failed to build application"

    info "Packaging benchmark application"
    pipenv run sam package \
        --template-file .aws-sam/build/template.yaml \
        --output-template-file .aws-sam/build/packaged-template.yaml \
        --s3-bucket "${S3_BUCKET}" \
        || die "Failed to package SAM application"

    info "Deploying benchmark application with stack name [${STACK_NAME}]"
    pipenv run sam deploy \
        --template-file .aws-sam/build/packaged-template.yaml \
        --stack-name "${STACK_NAME}" \
        --capabilities CAPABILITY_IAM \
        || die "Failed to deploy SAM application"

    URL=$(aws cloudformation describe-stacks \
          --stack-name pubsub-benchmark \
          --query 'Stacks[0].Outputs[0].OutputValue' \
          | sed -e 's/^"//' -e 's/"$//' \
          || die "Failed to fetch URL")
    info "Benchmark application deployed and available at [${URL}]"

    info "Running load test against [${URL}]"
    docker run \
        -e URL=${URL} \
        -i loadimpact/k6 run \
        --vus 20 --duration 300s -< k6_benchmark.js \
        || die "Failed to run load test"
}
  
sub_clean(){
    info "Deleting CloudFormation stack with name [${STACK_NAME}]"
    aws cloudformation delete-stack \
          --stack-name "${STACK_NAME}" \
          || die "Failed to delete CloudFormation stack"
    info "Waiting for stack [${STACK_NAME}] deletion"
    aws cloudformation wait stack-delete-complete \
          --stack-name "${STACK_NAME}" \
          || die "Timed out waiting for CloudFormation stack deletion"
    info "Stack [${STACK_NAME}] deleted"
}
  
subcommand=$1
case $subcommand in
    "" | "-h" | "--help")
        sub_help
        ;;
    *)
        shift
        sub_${subcommand} $@
        if [ $? = 127 ]; then
            echo "Error: '$subcommand' is not a known subcommand." >&2
            echo "       Run '$PROG_NAME --help' for a list of known subcommands." >&2
            exit 1
        fi
        ;;
esac