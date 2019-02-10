import base64
import json
import os
import time
import uuid

import boto3
from aws_xray_sdk.core import patch, xray_recorder
from aws_xray_sdk.core.models.segment import Segment


patch(('boto3',))
xray_client = boto3.client('xray')


def fanout_handler(event, _):
    print(f'Event:\n{json.dumps(event)}')
    current_segment = xray_recorder.current_segment()

    sns_client = boto3.client('sns')
    sns_client.publish(
        TopicArn=os.getenv('TOPIC_ARN'),
        Message=json.dumps({'default': json.dumps({'start_time': time.time(),
                                                   'trace_id': current_segment.trace_id,
                                                   'id': current_segment.id})}),
        MessageStructure='json'
    )

    dynamodb_client = boto3.client('dynamodb')
    dynamodb_client.put_item(
        TableName=os.getenv('TABLE_NAME'),
        Item={
            'id': {'S': current_segment.id},
            'start_time': {'N': str(time.time())},
            'trace_id': {'S': current_segment.trace_id}
        }
    )

    sqs_client = boto3.client('sqs')
    sqs_client.send_message(
        QueueUrl=os.getenv('QUEUE_URL'),
        MessageBody=json.dumps({'start_time': time.time(), 'trace_id': current_segment.trace_id,
                                'id': current_segment.id})
    )

    kinesis_client = boto3.client('kinesis')
    kinesis_client.put_record(
        StreamName=os.getenv('STREAM_NAME'),
        Data=json.dumps({'start_time': time.time(), 'trace_id': current_segment.trace_id,
                         'id': current_segment.id}),
        PartitionKey=str(uuid.uuid4())
        )


def sns_handler(event, _):
    message = json.loads(event['Records'][0]['Sns']['Message'])
    segment = Segment('sns_benchmark', traceid=message['trace_id'], parent_id=message['id'], sampled=True)
    segment.start_time = message['start_time']
    segment.put_annotation('scope', 'benchmark')
    segment.put_annotation('service', 'sns')
    segment.close()
    xray_client.put_trace_segments(TraceSegmentDocuments=[segment.serialize()])


def dynamodb_handler(event, _):
    message = event['Records'][0]['dynamodb']['NewImage']
    segment = Segment('dynamodb_benchmark', traceid=message['trace_id']['S'], parent_id=message['id']['S'], sampled=True)
    segment.start_time = float(message['start_time']['N'])
    segment.put_annotation('scope', 'benchmark')
    segment.put_annotation('service', 'dynamodb')
    segment.close()
    xray_client.put_trace_segments(TraceSegmentDocuments=[segment.serialize()])


def sqs_handler(event, _):
    message = json.loads(event['Records'][0]['body'])
    segment = Segment('sqs_benchmark', traceid=message['trace_id'], parent_id=message['id'], sampled=True)
    segment.start_time = message['start_time']
    segment.put_annotation('scope', 'benchmark')
    segment.put_annotation('service', 'sqs')
    segment.close()
    xray_client.put_trace_segments(TraceSegmentDocuments=[segment.serialize()])


def kinesis_handler(event, _):
    message = json.loads(base64.b64decode(event['Records'][0]['kinesis']['data']))
    segment = Segment('kinesis_benchmark', traceid=message['trace_id'], parent_id=message['id'], sampled=True)
    segment.start_time = message['start_time']
    segment.put_annotation('scope', 'benchmark')
    segment.put_annotation('service', 'kinesis')
    segment.close()
    xray_client.put_trace_segments(TraceSegmentDocuments=[segment.serialize()])
