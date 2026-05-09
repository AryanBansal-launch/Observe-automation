// Launch Management — application errors; infra-style lines live in mgmt_infra.opal
// Output columns (required): latest_timestamp, total_occurrences, error_msg, context

make_col level:string(parse_json(string(log)).level)
| filter level = "error" or level = "ERROR"
| filter label(^Cluster) = "{{REGION}}"
| filter log !~ 'Unexpected close' and log !~ 'Heartbeat timeout' and log !~ 'Disconnected from RMQ' and log !~ 'Health Check has failed' and log !~ '[Connection] Connection timeout' and log !~ 'Disconnected from RabbitMQ broker' and log !~ 'Failed to send messages: This server is not the leader for that topic-partition' and log !~ 'BrokerPool] Failed to connect to seed broker, trying another broker from the list: Connection timeout'
| make_col error_msg:string(parse_json(string(log)).message), context:string(parse_json(string(log)).context)
| statsby group_by(error_msg, context), latest_timestamp: max(timestamp), total_occurrences: count()
| sort desc(latest_timestamp)
