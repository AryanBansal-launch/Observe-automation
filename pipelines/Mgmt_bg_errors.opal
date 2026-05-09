// Launch Management Background Jobs — application errors; infra patterns in mgmt_bg_infra.opal
// Output columns (required): latest_timestamp, total_occurrences, error_msg, context

filter label(^Cluster) = "{{REGION}}"
| make_col level:string(parse_json(string(log)).level)
| filter level = "error" or level = "ERROR"
| filter log !~ 'Unexpected close' and log !~ 'Heartbeat timeout' and log !~ 'Health Check has failed' and log !~ 'Service Unavailable Exception' and log !~ '[Connection] Response JoinGroup' and log !~ 'Disconnected from RMQ. Trying to ' and log !~ 'Disconnected from RabbitMQ broker' and log !~ '[Consumer] Restarting the consumer in' and log !~ 'Error: read ECONNRESET at TCP.onStreamRead' and log !~ 'The coordinator is not aware of this member' and log !~ '[Connection] Connection error: read ECONNRESET' and log !~ '[Connection] Connection error: write after end' and log !~ 'The group is rebalancing, so a rejoin is needed' and log !~ 'This is not the correct coordinator for this group' and log !~ 'Client network socket disconnected before secure TLS connection was established' and log !~ 'There is no leader for this topic-partition as we are in the middle of a leadership election'
| make_col error_msg:string(parse_json(string(log)).message), context:string(parse_json(string(log)).context)
| statsby group_by(error_msg, context), latest_timestamp: max(timestamp), total_occurrences: count()
| sort desc(latest_timestamp)
