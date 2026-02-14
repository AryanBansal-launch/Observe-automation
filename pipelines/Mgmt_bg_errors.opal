// Default error-extraction pipeline for log datasets where each row has a "log" column
// with JSON like: { "level": "error", "message": "...", "context": "..." }
// Output columns (required for extract_errors.py table): latest_timestamp, total_occurrences, error_msg, context

make_col level:string(parse_json(string(log)).level)
| filter level = "error" or level = "ERROR"
| filter label(^Cluster) = "{{REGION}}"
| filter log !~ "The group is rebalancing, so a rejoin is needed"
| make_col error_msg:string(parse_json(string(log)).message), context:string(parse_json(string(log)).context)
| statsby group_by(error_msg, context), latest_timestamp: max(timestamp), total_occurrences: count()
| sort desc(latest_timestamp)
