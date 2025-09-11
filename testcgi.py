<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>System Healthcheck Report - {{ inventory_hostname }}</title>
    <style>
        body {
            font-family: Arial, sans-serif;
            margin: 20px;
            line-height: 1.6;
        }
        h1 {
            color: #333;
            border-bottom: 2px solid #333;
            padding-bottom: 10px;
        }
        h2 {
            color: #444;
            margin-top: 30px;
            background-color: #f0f0f0;
            padding: 8px;
            border-left: 4px solid #666;
        }
        table {
            width: 100%;
            border-collapse: collapse;
            margin-top: 20px;
            margin-bottom: 30px;
        }
        th, td {
            border: 1px solid #ddd;
            padding: 8px;
            text-align: left;
        }
        th {
            background-color: #f2f2f2;
            position: sticky;
            top: 0;
        }
        tr:nth-child(even) {
            background-color: #f9f9f9;
        }
        tr:hover {
            background-color: #f1f1f1;
        }
        .status-positive {
            color: #28a745; /* Green */
            font-weight: bold;
        }
        .status-negative {
            color: #dc3545; /* Red */
            font-weight: bold;
        }
        .status-warning {
            color: #ffc107; /* Yellow */
            font-weight: bold;
        }
        .scrollable {
            max-height: 200px;
            overflow-y: auto;
            border: 1px solid #eee;
            padding: 5px;
        }
        pre {
            white-space: pre-wrap;
            word-wrap: break-word;
            margin: 0;
            font-family: monospace;
        }
        /* Uniform column widths */
        td:first-child, th:first-child {
            width: 20%; /* Fixed width for the label column */
            vertical-align: top;
        }
        td:last-child, th:last-child {
            width: 80%; /* Fixed width for the value column */
        }
    </style>
</head>
<body>
    <h1>System Healthcheck Report - {{ ansible_hostname }}</h1>
    <p><strong>Generated on:</strong> {{ ansible_date_time.iso8601 }}</p>
    <p><strong>Hostname:</strong> {{ ansible_hostname }}</p>

    <h2>Overall Status Summary</h2>
<table>
    <thead>
        <tr>
            <th>Check</th>
            <th>Status</th>
        </tr>
    </thead>
    <tbody>
        <tr>
            <td>Disk Usage</td>
            <td>
                {% if disk_used is defined and disk_used.stdout %}
                    <span class="{% if disk_used.stdout | int >= disk_threshold %}status-negative{% else %}status-positive{% endif %}">
                        {% if disk_used.stdout | int >= disk_threshold %}Critical{% else %}Healthy{% endif %}
                    </span>
                {% else %} N/A {% endif %}
            </td>
        </tr>
        <tr>
            <td>Free Memory</td>
            <td>
                {% if mem_free_mb is defined %}
                    <span class="{% if mem_free_mb | int <= mem_threshold %}status-negative{% else %}status-positive{% endif %}">
                        {% if mem_free_mb | int <= mem_threshold %}Critical{% else %}Healthy{% endif %}
                    </span>
                {% else %} N/A {% endif %}
            </td>
        </tr>
        <tr>
            <td>Load Average</td>
            <td>
                {% if ansible_loadavg is defined and ansible_loadavg['1m'] is defined %}
                    <span class="{% if ansible_loadavg['1m'] | float >= load_threshold %}status-warning{% else %}status-positive{% endif %}">
                        {% if ansible_loadavg['1m'] | float >= load_threshold %}Warning{% else %}Healthy{% endif %}
                    </span>
                {% else %} N/A {% endif %}
            </td>
        </tr>
        <tr>
            <td>Failed Services</td>
            <td>
                {% if failed_services is defined and failed_services.stdout %}
                    <span class="status-negative">Critical</span>
                {% else %}
                    <span class="status-positive">Healthy</span>
                {% endif %}
            </td>
        </tr>
        <tr>
            <td>System Error Logs</td>
            <td>
                {% if error_logs is defined and error_logs.stdout %}
                    <span class="status-warning">Warning</span>
                {% else %}
                    <span class="status-positive">Healthy</span>
                {% endif %}
            </td>
        </tr>
        <tr>
            <td>Kubernetes Pods</td>
            <td>
                {% if non_running_pods is defined and non_running_pods.stdout %}
                    <span class="status-warning">Issues Found</span>
                {% else %}
                    <span class="status-positive">Healthy</span>
                {% endif %}
            </td>
        </tr>
    </tbody>
</table>
   

    <h3>System Metrics</h2>
    <table>
        <tbody>
            <tr>
                <td><strong>OS Family</strong></td>
                <td>{{ ansible_distribution }} - {{ ansible_distribution_version }}</td>
            </tr>
            <tr>
                <td><strong>BIOS Vendor</strong></td>
                <td>
                    {% set vendor = firmware_details | select('search', 'Vendor') | list %}
                    {% if firmware_details is defined and vendor | length > 0 %}
                        {{ vendor[0] | regex_replace('^.*Vendor:\\s*', '') | trim }}
                    {% else %}
                        N/A
                    {% endif %}
                </td>
            </tr>
            <tr>
                <td><strong>BIOS Version</strong></td>
                <td>
                    {% set version = firmware_details | select('search', 'Version') | list %}
                    {% if firmware_details is defined and version | length > 0 %}
                        {{ version[0] | regex_replace('^.*Version:\\s*', '') | trim }}
                    {% else %}
                        N/A
                    {% endif %}
                </td>
            </tr>
            <tr>
                <td><strong>BIOS Release Date</strong></td>
                <td>
                    {% set release_date = firmware_details | select('search', 'Release Date') | list %}
                    {% if firmware_details is defined and release_date | length > 0 %}
                        {{ release_date[0] | regex_replace('^.*Release Date:\\s*', '') | trim }}
                    {% else %}
                        N/A
                    {% endif %}
                </td>
            </tr>
            <tr>
                <td><strong>Disk Usage (%)</strong></td>
                <td>
                    {% if disk_used is defined and disk_used.stdout %}
                        <span class="{% if disk_used.stdout | int >= disk_threshold %}status-negative{% else %}status-positive{% endif %}">
                            {{ disk_used.stdout }}%
                        </span>
                    {% else %}
                        N/A
                    {% endif %}
                </td>
            </tr>
            <tr>
                <td><strong>Free Memory</strong></td>
                <td>
                    {% if mem_free_mb is defined %}
                        <span class="{% if mem_free_mb | int <= mem_threshold %}status-negative{% else %}status-positive{% endif %}">
                            {{ mem_free_mb }} MB
                        </span>
                    {% else %}
                        N/A
                    {% endif %}
                </td>
            </tr>
            <tr>
                <td><strong>Load Average</strong></td>
                <td>
                    {% if ansible_loadavg is defined and ansible_loadavg['1m'] is defined %}
                        <span class="{% if ansible_loadavg['1m'] | float >= load_threshold %}status-negative{% else %}status-positive{% endif %}">
                            {{ ansible_loadavg['1m'] }}
                        </span>
                    {% else %}
                        N/A
                    {% endif %}
                </td>
            </tr>
            <tr>
                <td><strong>Timezone</strong></td>
                <td>
                    {% if timezone_info is defined and timezone_info.stdout %}
                        <pre>{{ timezone_info.stdout }}</pre>
                    {% else %}
                        N/A
                    {% endif %}
                </td>
            </tr>
            <tr>
                <td><strong>Installed Packages</strong></td>
                <td>
                    {% if ansible_facts.packages is defined %}
                        {% if ansible_os_family == 'RedHat' %}
                            <div class="scrollable">
                                <pre>{% for package in ansible_facts.packages.keys() | sort %}{{ package }}
{% endfor %}</pre>
                            </div>
                        {% elif ansible_os_family == 'Debian' %}
                            <div class="scrollable">
                                <pre>{% for package in ansible_facts.packages.keys() | sort %}{{ package }}
{% endfor %}</pre>
                            </div>
                        {% else %}
                            <div class="scrollable">
                                <pre>Package information not available for this OS</pre>
                            </div>
                        {% endif %}
                    {% else %}
                        <div class="scrollable">
                            <pre>N/A</pre>
                        </div>
                    {% endif %}
                </td>
            </tr>
            <tr>
                <td><strong>Running Services</strong></td>
                <td>
                    {% if running_services is defined and running_services.stdout %}
                        <div class="scrollable">
                            <pre><span class="status-positive">{{ running_services.stdout }}</span></pre>
                        </div>
                    {% else %}
                        N/A
                    {% endif %}
                </td>
            </tr>
            <tr>
                <td><strong>Failed Services</strong></td>
                <td>
                    {% if failed_services is defined and failed_services.stdout %}
                        <div class="scrollable">
                            <pre><span class="status-negative">{{ failed_services.stdout }}</span></pre>
                        </div>
                    {% else %}
                        None
                    {% endif %}
                </td>
            </tr>
            <tr>
                <td><strong>System Error Logs</strong></td>
                <td>
                    {% if error_logs is defined and error_logs.stdout %}
                        <div class="scrollable">
                            <pre>{{ error_logs.stdout }}</pre>
                        </div>
                    {% else %}
                        None
                    {% endif %}
                </td>
            </tr>
            <tr>
                <td><strong>Logrotate configured on</strong></td>
                <td>
                    {% if logrotate_services_fact is defined and logrotate_services_fact %}
                        <div class="scrollable">
                            <pre>{% for service in logrotate_services_fact %}{{ service }}
{% endfor %}</pre>
                        </div>
                    {% else %}
                        N/A
                    {% endif %}
                </td>
            </tr>
        </tbody>
    </table>

    <h2>Kubernetes Metrics</h2>
    <table>
        <tbody>
            <tr>
                <td><strong>All Pods</strong></td>
                <td>
                    {% if all_pods is defined and all_pods.stdout %}
                        <div class="scrollable">
                            <pre>{{ all_pods.stdout }}</pre>
                        </div>
                    {% else %}
                        N/A
                    {% endif %}
                </td>
            </tr>
            <tr>
                <td><strong>Non-Running Pods</strong></td>
                <td>
                    {% if non_running_pods is defined and non_running_pods.stdout %}
                        <div class="scrollable">
                            <pre>{{ non_running_pods.stdout }}</pre>
                        </div>
                    {% else %}
                        None
                    {% endif %}
                </td>
            </tr>
            <tr>
                <td><strong>Pods with 0 Readiness</strong></td>
                <td>
                    {% if zero_ready_pods_fact is defined and zero_ready_pods_fact %}
                        <div class="scrollable">
                            <pre>{{ zero_ready_pods_fact }}</pre>
                        </div>
                    {% else %}
                        None
                    {% endif %}
                </td>
            </tr>
            <tr>
                <td><strong>Warning Events</strong></td>
                <td>
                    {% if warnings is defined and warnings.stdout %}
                        <div class="scrollable">
                            <pre>{{ warnings.stdout }}</pre>
                        </div>
                    {% else %}
                        None
                    {% endif %}
                </td>
            </tr>
            <tr>
                <td><strong>Top Pods by CPU</strong></td>
                <td>
                    {% if top_pods_cpu_fact is defined and top_pods_cpu_fact %}
                        <div class="scrollable">
                            <pre>{{ top_pods_cpu_fact }}</pre>
                        </div>
                    {% else %}
                        N/A
                    {% endif %}
                </td>
            </tr>
            <tr>
                <td><strong>Top Nodes by CPU</strong></td>
                <td>
                    {% if top_nodes_cpu is defined and top_nodes_cpu.stdout %}
                        <div class="scrollable">
                            <pre>{{ top_nodes_cpu.stdout }}</pre>
                        </div>
                    {% else %}
                        N/A
                    {% endif %}
                </td>
            </tr>
        </tbody>
    </table>
</body>
</html>
