// Example for logs where the message field is named "msg" and context is optional.
// Use this as a template when your service uses different JSON keys.
// Output columns (required): latest_timestamp, total_occurrences, error_msg, context

make_col level:string(parse_json(string(log)).level)
| filter level = "error" or level = "ERROR"
| filter label(^Cluster) = "{{REGION}}"
| make_col
    error_msg:string(parse_json(string(log)).msg),
    context:string(parse_json(string(log)).context)
| statsby group_by(error_msg, context), latest_timestamp: max(timestamp), total_occurrences: count()
| sort desc(latest_timestamp)
