// Launch Logs background jobs — application errors; infra patterns in launch_logs_bg_infra.opal
// Output columns (required): latest_timestamp, total_occurrences, error_msg, context

make_col level:string(parse_json(string(log)).level)
| filter level = "error" or level = "ERROR"
| filter label(^Cluster) = "{{REGION}}"
| filter log !~ 'Unexpected close' and log !~ 'Heartbeat timeout' and log !~ '[Connection] Response' and log !~ 'Health Check has failed' and log !~ 'Restarting the consumer in' and log !~ '[Connection] Connection error' and log !~ 'Disconnected from RabbitMQ broker' and log !~ 'Disconnected from RMQ. Trying to reconnect' and log !~ 'The group is rebalancing, so a rejoin is needed' and log !~ 'This is not the correct coordinator of the group' and log !~ 'This is not the correct coordinator for this group'
| make_col error_msg:string(parse_json(string(log)).message), context:string(parse_json(string(log)).context)
| statsby group_by(error_msg, context), latest_timestamp: max(timestamp), total_occurrences: count()
| sort desc(latest_timestamp)
