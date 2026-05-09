// Launch Nginx — infrastructure-ish stderr / Redis / upstream noise (narrower than main nginx pipeline)
// Output columns (required): latest_timestamp, total_occurrences, error_msg, context

filter label(^Cluster) = "{{REGION}}" or is_null(label(^Cluster))
| make_col httpstatus_code:string(parse_json(string(log))['http.status_code'])
| filter is_null(httpstatus_code) or (httpstatus_code != "404" and httpstatus_code != "200" and httpstatus_code != "500")
| filter stream = "stderr"
| filter log ~ 'unexpected DNS response' or log ~ 'recv() failed' or log ~ 'wrong ident' or log ~ 'send() failed' or log ~ 'Error while connecting to redis: timeout'
| make_col error_msg:string(log), context:string(stream)
| statsby group_by(error_msg, context), latest_timestamp: max(timestamp), total_occurrences: count()
| sort desc(latest_timestamp)
