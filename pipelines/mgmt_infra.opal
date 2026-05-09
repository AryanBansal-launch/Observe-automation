// Launch Management — infrastructure signals (Kafka / RabbitMQ / health / timeouts)
// Output columns (required): latest_timestamp, total_occurrences, error_msg, context

make_col level:string(parse_json(string(log)).level)
| filter level = "error" or level = "ERROR"
| filter label(^Cluster) = "{{REGION}}" or is_null(label(^Cluster))
| filter log ~ 'Health Check has failed' or log ~ 'Disconnected from RabbitMQ broker' or log ~ 'Failed to send messages: This server is not the leader for that topic-partition' or log ~ '[Connection] Connection timeout' or log ~ '[BrokerPool] Failed to connect to seed broker, trying another broker from the list: Connection timeout' or log ~ 'Disconnected from RMQ' or log ~ 'Heartbeat timeout'
| statsby latest_timestamp: max(timestamp), total_occurrences: count(), group_by()
| make_col error_msg: "Infrastructure errors (patterns: health check, RabbitMQ disconnect, Kafka leader, timeouts, heartbeat)", context: "aggregate row - use Link for Observe query window"
