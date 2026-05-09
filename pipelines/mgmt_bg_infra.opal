// Launch Management Background Jobs — infrastructure / coordinator / consumer instability
// Output columns (required): latest_timestamp, total_occurrences, error_msg, context

filter label(^Cluster) = "{{REGION}}" or is_null(label(^Cluster))
| make_col level:string(parse_json(string(log)).level)
| filter level = "error" or level = "ERROR"
| filter log ~ 'The coordinator is not aware of this member' or log ~ 'The group is rebalancing, so a rejoin is needed' or log ~ 'This is not the correct coordinator for this group' or log ~ 'Health Check has failed' or log ~ 'Disconnected from RabbitMQ broker' or log ~ 'Service Unavailable Exception' or log ~ 'There is no leader for this topic-partition as we are in the middle of a leadership election' or log ~ 'Error: read ECONNRESET at TCP.onStreamRead' or log ~ '[Connection] Connection error: read ECONNRESET' or log ~ '[Consumer] Restarting the consumer in' or log ~ '[Connection] Response JoinGroup' or log ~ '14 UNAVAILABLE: No connection established. Last error: Error: connect ECONNREFUSED 127.0.0.1:50001' or log ~ 'Unexpected close' or log ~ 'Heartbeat timeout' or log ~ 'read ECONNRESET' or log ~ 'Client network socket disconnected before secure TLS connection was established'
| make_col error_msg:string(parse_json(string(log)).message), context:string(parse_json(string(log)).context)
| statsby group_by(error_msg, context), latest_timestamp: max(timestamp), total_occurrences: count()
| sort desc(latest_timestamp)
