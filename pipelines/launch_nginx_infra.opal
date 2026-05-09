// Launch Nginx — infrastructure-ish stderr / Redis / upstream noise (narrower than main nginx pipeline)
// Output columns (required): latest_timestamp, total_occurrences, error_msg, context

filter label(^Cluster) = "{{REGION}}" or is_null(label(^Cluster))
| make_col httpstatus_code:string(parse_json(string(log))['http.status_code'])
| filter is_null(httpstatus_code) or (httpstatus_code != "404" and httpstatus_code != "200" and httpstatus_code != "500")
| filter stream = "stderr"
| filter log ~ 'unexpected DNS response' or log ~ 'recv() failed' or log ~ 'wrong ident' or log ~ 'send() failed' or log ~ 'Error while connecting to redis: timeout'
| statsby latest_timestamp: max(timestamp), total_occurrences: count(), group_by()
| make_col error_msg: "Infrastructure errors (stderr: DNS/redis/socket)", context: "aggregate row - use Link for Observe query window"
