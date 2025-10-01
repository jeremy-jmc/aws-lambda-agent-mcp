const { App, AwsLambdaReceiver } = require('@slack/bolt');
const AWS = require('aws-sdk');

// Initialize AWS SDK
const lambda = new AWS.Lambda({
    region: process.env.AWS_REGION || 'us-east-1'
});

const sqs = new AWS.SQS({
    region: process.env.AWS_REGION || 'us-east-1'
});

// SQS helper functions
const sendMessageToSQS = async (event, logger) => {
    const queueUrl = process.env.SQS_QUEUE_URL;
    if (!queueUrl) {
        throw new Error('SQS_QUEUE_URL environment variable is not set');
    }

    // Create unique message ID using channel and timestamp
    const messageId = `${event.channel}_${event.ts}`;
    
    const params = {
        QueueUrl: queueUrl,
        MessageBody: JSON.stringify(event),
        MessageGroupId: event.channel, // For FIFO queues
        MessageDeduplicationId: messageId, // Prevent duplicates
        MessageAttributes: {
            'MessageId': {
                DataType: 'String',
                StringValue: messageId
            },
            'Channel': {
                DataType: 'String',
                StringValue: event.channel
            },
            'Timestamp': {
                DataType: 'String',
                StringValue: event.ts
            }
        }
    };

    try {
        const result = await sqs.sendMessage(params).promise();
        logger.info(`Message queued successfully with ID: ${messageId}`, result);
        return result;
    } catch (error) {
        logger.error('Error sending message to SQS:', error);
        throw error;
    }
};

const deleteMessageFromSQS = async (messageId, logger) => {
    const queueUrl = process.env.SQS_QUEUE_URL;
    if (!queueUrl) {
        logger.warn('SQS_QUEUE_URL not set, cannot delete message');
        return;
    }

    try {
        // First, receive messages to find the one to delete
        const receiveParams = {
            QueueUrl: queueUrl,
            MessageAttributeNames: ['MessageId'],
            MaxNumberOfMessages: 10,
            WaitTimeSeconds: 1
        };

        const messages = await sqs.receiveMessage(receiveParams).promise();
        
        if (messages.Messages) {
            for (const message of messages.Messages) {
                if (message.MessageAttributes && 
                    message.MessageAttributes.MessageId && 
                    message.MessageAttributes.MessageId.StringValue === messageId) {
                    
                    const deleteParams = {
                        QueueUrl: queueUrl,
                        ReceiptHandle: message.ReceiptHandle
                    };
                    
                    await sqs.deleteMessage(deleteParams).promise();
                    logger.info(`Deleted message from SQS: ${messageId}`);
                    return;
                }
            }
        }
        
        logger.info(`Message ${messageId} not found in SQS queue (omitting deletion)`);
    } catch (error) {
        logger.error('Error deleting message from SQS:', error);
    }
};

// Initialize your custom receiver
const awsLambdaReceiver = new AwsLambdaReceiver({
    signingSecret: process.env.SLACK_SIGNING_SECRET,
});

// Initializes your app with your bot token and the AWS Lambda ready receiver
const app = new App({
    token: process.env.SLACK_BOT_TOKEN,
    receiver: awsLambdaReceiver,

    // When using the AwsLambdaReceiver, processBeforeResponse can be omitted.
    // If you use other Receivers, such as ExpressReceiver for OAuth flow support
    // then processBeforeResponse: true is required. This option will defer sending back
    // the acknowledgement until after your handler has run to ensure your handler
    // isn't terminated early by responding to the HTTP request that triggered it.

    // receiver.processBeforeResponse: true

});


// When a user joins the team, send a message in a predefined channel asking them to introduce themselves
// ! IMPORTANT: https://github.com/slackapi/python-slack-sdk/issues/335
// * Check: https://api.slack.com/events/app_mention
app.event('message', async ({ event, client, logger }) => {
    const { user_id: botUserId } = await app.client.auth.test();
    let threadMessages = null, wm = false;
    const BOT_TAG = `<@${botUserId}>`;
    logger.info('Bot TAG: ', BOT_TAG);

    const wasBotMentioned = async (messages) => {
        return messages.some(msg => {
            if (msg.message && msg.message.includes(BOT_TAG)) {
                return true;
            }
            return false;
        });
    }

    if (event.subtype != undefined)
        return; // Ignore messages with subtypes (e.g., message edits, reactions, channel joins, channel lefts, etc.)

    logger.info('Received message event:', event);
    logger.info(`Environment: ${process.env.NODE_ENV}`);

    event.message = event.text;
    event.bot_tag = BOT_TAG;
    event.blocks = [];
    // Make a copy of the event object to work with
    const channelInfo = await client.conversations.info({ channel: event.channel });
    const { user } = await client.users.info({ user: event.user });

    logger.info(user);
    logger.info(channelInfo);
    logger.info(event);

    logger.info(`User ${user.profile.display_name} sent message to the channel ${channelInfo.channel.name}`);
    
    const messageId = `${event.channel}_${event.ts}`;
    
    if (!event.thread_ts) {
        event.thread_ts = event.ts;
    }

    try {
        wm = await wasBotMentioned([event]);
        logger.info('Was bot mentioned in message:', wm);
        
        if (!wm) {
            logger.info('Bot was not mentioned, scheduling message for evaluation in 3 minutes via SQS');
            
            // Check if there's already a queued message in this channel and replace it
            try {
                await deleteMessageFromSQS(messageId, logger);
            } catch (error) {
                logger.warn('Error checking/deleting existing message from SQS:', error);
            }
            
            // Queue the new message for delayed processing
            try {
                await sendMessageToSQS(event, logger);
                logger.info(`Message scheduled for evaluation with ID: ${messageId}`);
            } catch (error) {
                logger.error('Failed to schedule message in SQS:', error);
            }
            
        } else {
            logger.info('Bot was mentioned, sending immediately to the Evaluator Lambda for processing');
            
            try {
                await deleteMessageFromSQS(messageId, logger);
            } catch (error) {
                logger.debug('No pending message to cancel:', error);
            }
            
            // Send message to Agent Lambda whether local or deployed
            logger.info('Sending message to Agent Lambda:', event);
            try {
                logger.info('Calling Agent Lambda...');
                if (process.env.NODE_ENV === 'dev') {
                    let localEvaluatorUrl = process.env.LOCAL_EVALUATOR_URL;
                    localEvaluatorUrl = localEvaluatorUrl.replace(/https?:\/\/(localhost|127\.0\.0\.1)(:\d+)?/i, 'http://host.docker.internal$2');
                    logger.info(`Calling local agent at: ${localEvaluatorUrl}`);
                    
                    // Fire and forget - don't await the response
                    fetch(localEvaluatorUrl, {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json'
                        },
                        body: JSON.stringify(event)
                    }).catch(error => {
                        logger.error('Error sending request to local evaluator:', error);
                    });
                    
                    logger.info('Local evaluator request sent asynchronously');
                } else {
                    // Get the Evaluator Lambda ARN from environment variable
                    const evaluatorLambdaArn = process.env.EVALUATOR_LAMBDA_ARN;
                    
                    if (!evaluatorLambdaArn) {
                        throw new Error('EVALUATOR_LAMBDA_ARN environment variable is not set');
                    }
                    
                    // Log the Lambda function ARN
                    logger.info(`Invoking Lambda function: ${evaluatorLambdaArn}`);
                    
                    const params = {
                        FunctionName: evaluatorLambdaArn,
                        InvocationType: 'Event', // Asynchronous invocation  
                        Payload: JSON.stringify({
                            body: JSON.stringify(event),
                            headers: {
                                'Content-Type': 'application/json'
                            },
                        })
                    };
                    
                    logger.info('Lambda invoke params:', JSON.stringify(params, null, 2));
                    // Invoke the Lambda function asynchronously
                    try {
                        logger.info('About to invoke Lambda asynchronously...');
                        const result = await lambda.invoke(params).promise();
                        logger.info('Lambda invocation submitted:', {
                            StatusCode: result.StatusCode,
                            FunctionError: result.FunctionError,
                            ExecutedVersion: result.ExecutedVersion
                        });
                        
                        // For async invocations, StatusCode 202 means it was accepted
                        if (result.StatusCode === 202) {
                            logger.info('✅ Lambda invocation accepted for async execution');
                        } else {
                            logger.warn('⚠️ Unexpected status code:', result.StatusCode);
                        }
                    } catch (invocationError) {
                        console.error('=== LAMBDA INVOCATION FAILED ===');
                        console.error('Error details:', invocationError);
                        console.error('Error code:', invocationError.code);
                        console.error('Error message:', invocationError.message);
                        logger.error('❌ Lambda invocation failed:', invocationError);
                        throw invocationError;
                    }
                }
                logger.info('Evaluator Lambda invoked successfully');
            } catch (requestError) {
                logger.error('Error calling Evaluator Lambda:', requestError);
                
                if (process.env.NODE_ENV === 'dev') {
                    await client.chat.postMessage({
                        channel: event.channel,
                        text: `Error processing with User: ${requestError.message}`,
                        thread_ts: event.thread_ts || event.ts,
                    });
                }
            }

        }
        
    }
    catch (error) {
        logger.error(error);
    }
});


// Handle the Lambda function event
module.exports.handler = async (event, context, callback) => {
    console.log('Starting Slack Event Listener...');
    console.log('Using Slack Bot Token:', process.env.SLACK_BOT_TOKEN);

    const handler = await awsLambdaReceiver.start();
    return handler(event, context, callback);
}

/* 
https://tools.slack.dev/bolt-js/deployments/aws-lambda/
https://docs.aws.amazon.com/lambda/latest/dg/lambda-typescript.html
https://docs.aws.amazon.com/es_es/lambda/latest/dg/lambda-typescript.html
https://github.com/slackapi/bolt-js/blob/main/examples/getting-started-typescript/src/app.ts
https://tools.slack.dev/bolt-js/concepts/ai-apps
*/