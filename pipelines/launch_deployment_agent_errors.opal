// Launch Deployment Agent: fatal/error level, exclude specific error messages
// Output columns (required for extract_errors.py): latest_timestamp, total_occurrences, error_msg, context

filter label(^Cluster) = "{{REGION}}"
| make_col level:string(parse_json(string(log)).level)
| filter level = "fatal" or level = "error" or level = "ERROR"
| make_col errormessage:string(parse_json(string(log))["error.message"])
| filter is_null(errormessage) or (errormessage != "Nodeops error: Container exited with status code 1" and errormessage != "exit status 1")
| make_col error_msg:string(coalesce(errormessage, log)), context:string(level)
| statsby group_by(error_msg, context), latest_timestamp: max(timestamp), total_occurrences: count()
| sort desc(latest_timestamp)
