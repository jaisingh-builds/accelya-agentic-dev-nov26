# What an AI assistant typically returns for:
#   "write a lambda that processes uploaded files"
# with NO repository context. Kept as a review exercise - DO NOT DEPLOY.
# Spot at least 6 problems using docs/HITL_REVIEW_TEMPLATE.md.

import boto3
import json

s3 = boto3.client('s3', region_name='us-east-1')

def lambda_handler(event, context):
    print("Received event: " + json.dumps(event))
    bucket = event['Records'][0]['s3']['bucket']['name']
    key = event['Records'][0]['s3']['object']['key']
    obj = s3.get_object(Bucket=bucket, Key=key)
    content = obj['Body'].read().decode('utf-8')
    print("File content: " + content)
    orders = []
    for line in content.split('\n'):
        order_id, user_id, amount = line.split(',')[:3]
        orders.append({'order_id': order_id, 'user_id': user_id, 'amount': amount})
    try:
        s3.put_object(Bucket=bucket, Key='processed/' + key, Body=json.dumps(orders))
    except Exception as e:
        pass
    return {'statusCode': 200, 'body': 'done'}

# Suggested IAM policy:
# { "Effect": "Allow", "Action": "s3:*", "Resource": "*" }
