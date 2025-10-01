import json
import boto3
import os
import logging

logger = logging.getLogger()
logger.setLevel(logging.INFO)

lambda_client = boto3.client('lambda', region_name=os.environ.get('AWS_REGION', 'us-east-1'))

def lambda_handler(event, context):
    """
    SQS Message Processor Lambda Handler
    
    Processes delayed Slack messages from SQS and forwards them to the Evaluator Lambda
    """
    logger.info('SQS Message Processor started')
    logger.info(f'Received SQS event: {json.dumps(event, indent=2)}')
    
    results = []
    
    # Process each record from SQS
    for record in event.get('Records', []):
        try:
            logger.info(f'Processing SQS record: {record}')
            
            # Parse the Slack event from the message body
            slack_event = json.loads(record['body'])
            logger.info(f'Parsed Slack event: {json.dumps(slack_event, indent=2)}')
            
            # Get the Evaluator Lambda ARN from environment variables
            evaluator_lambda_arn = os.environ.get('EVALUATOR_LAMBDA_ARN')
            
            if not evaluator_lambda_arn:
                raise ValueError('EVALUATOR_LAMBDA_ARN environment variable is not set')
            
            logger.info(f'Invoking Evaluator Lambda: {evaluator_lambda_arn}')
            
            # Prepare the payload for the Evaluator Lambda
            payload = {
                'body': json.dumps(slack_event),
                'headers': {
                    'Content-Type': 'application/json'
                }
            }
            
            logger.info(f'Lambda invoke payload: {json.dumps(payload, indent=2)}')
            
            # Invoke the Evaluator Lambda asynchronously
            response = lambda_client.invoke(
                FunctionName=evaluator_lambda_arn,
                InvocationType='Event',  # Asynchronous invocation
                Payload=json.dumps(payload)
            )
            
            logger.info(f'Evaluator Lambda invocation result: {response}')
            
            if response['StatusCode'] == 202:
                logger.info('Evaluator Lambda invocation accepted for async execution')
                results.append({
                    'messageId': record.get('messageId'),
                    'status': 'success',
                    'statusCode': response['StatusCode']
                })
            else:
                logger.warning(f'Unexpected status code: {response["StatusCode"]}')
                results.append({
                    'messageId': record.get('messageId'),
                    'status': 'warning',
                    'statusCode': response['StatusCode']
                })
                
        except Exception as error:
            logger.error(f'Error processing SQS record: {str(error)}')
            logger.error(f'Record that failed: {json.dumps(record, indent=2)}')
            
            results.append({
                'messageId': record.get('messageId'),
                'status': 'error',
                'error': str(error)
            })
            
            # Re-raise error to send message to DLQ
            raise error
    
    logger.info('SQS Message Processor completed')
    logger.info(f'Processing results: {json.dumps(results, indent=2)}')
    
    return {
        'statusCode': 200,
        'body': json.dumps({
            'message': 'SQS messages processed successfully',
            'results': results
        })
    }