// Launch Logs background jobs — infra-style connection / consumer issues
// Output columns (required): latest_timestamp, total_occurrences, error_msg, context

filter label(^Cluster) = "{{REGION}}" or is_null(label(^Cluster))
| filter log ~ 'Restarting the consumer in' or log ~ 'The group is rebalancing, so a rejoin is needed' or log ~ 'This is not the correct coordinator of the group' or log ~ 'Disconnected from RabbitMQ broker' or log ~ 'Health Check has failed' or log ~ 'Disconnected from RMQ. Trying to reconnect' or log ~ 'Heartbeat timeout' or log ~ '[Connection] Response' or log ~ 'This is not the correct coordinator for this group' or log ~ '[Connection] Connection error' or log ~ '[Consumer] Crash' or log ~ 'Unexpected close'
| make_col error_msg:string(parse_json(string(log)).message), context:string(parse_json(string(log)).context)
| statsby group_by(error_msg, context), latest_timestamp: max(timestamp), total_occurrences: count()
| sort desc(latest_timestamp)
