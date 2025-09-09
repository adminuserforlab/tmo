<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8"/>
    <meta name="viewport" content="width=device-width, initial-scale=1"/>
    <title>System Healthcheck Report - {{ inventory_hostname }}</title>
    <style>
        /* ---- Base ---- */
        body { 
            font-family: "Segoe UI", Roboto, Arial, sans-serif; 
            margin: 0; padding: 20px; 
            background: #f7f9fc; 
            color: #222; 
        }
        h1, h2 { margin: 0 0 12px 0; }
        h1 { font-size: 22px; font-weight: 700; color: #1e3a8a; }
        h2 { font-size: 18px; font-weight: 600; color: #374151; margin-top: 30px; }
        .meta { font-size: 13px; color: #555; margin-top: 4px; }

        /* ---- Containers ---- */
        .card {
            background: #fff;
            border-radius: 10px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.08);
            padding: 16px 20px;
            margin-bottom: 24px;
        }
        .section { margin-top: 20px; }

        /* ---- Badges ---- */
        .badges { display:flex; gap:12px; flex-wrap: wrap; }
        .badge {
            padding: 8px 14px;
            border-radius: 20px;
            font-weight:600;
            font-size: 13px;
            display:flex; align-items:center; gap:8px;
            box-shadow: 0 1px 3px rgba(0,0,0,0.1);
        }
        .badge .icon { font-size: 16px; }
        .status-positive { background:#dcfce7; color:#166534; }
        .status-warning  { background:#fef9c3; color:#854d0e; }
        .status-negative { background:#fee2e2; color:#991b1b; }
        .status-unknown  { background:#e0e7ff; color:#3730a3; }

        /* ---- Tables ---- */
        table { width:100%; border-collapse:collapse; margin-top:12px; }
        th, td { border:1px solid #e5e7eb; padding:8px 10px; text-align:left; font-size:14px; }
        th { background:#f1f5f9; font-weight:600; color:#374151; }
        tr:nth-child(even){ background:#f9fafb; }
        td:first-child { font-weight:600; width: 220px; color:#1f2937; }

        /* ---- Scrollable areas ---- */
        .scrollable { 
            max-height:220px; 
            overflow:auto; 
            border:1px solid #e5e7eb; 
            border-radius:6px; 
            background:#fdfdfd; 
            padding:8px; 
        }
        pre { margin:0; font-family: Consolas, monospace; font-size: 13px; white-space:pre-wrap; }

        /* ---- Problems ---- */
        .problem-item { 
            margin:6px 0; 
            padding:8px 10px; 
            border-radius:6px; 
            background:#fff; 
            border-left:4px solid #f87171; 
            box-shadow: 0 1px 2px rgba(0,0,0,0.05); 
        }
        .muted { color:#6b7280; font-size:13px; }
        .col-3 { display:flex; gap:16px; margin-top:12px; }
        .col-3 > div { flex:1; }

        /* ---- Header ---- */
        .header { display:flex; justify-content:space-between; align-items:flex-start; margin-bottom:20px; }
        .overall { font-size:15px; margin-top:8px; }
    </style>
</head>
<body>
    <!-- Header -->
    <div class="header card">
        <div>
            <h1>System Healthcheck Report - {{ ansible_hostname }}</h1>
            <div class="meta">
                Generated on: {{ ansible_date_time.iso8601 }} &nbsp;•&nbsp; Hostname: {{ ansible_hostname }}
            </div>
            <div class="overall">
                {% if (services_issues == false) and (pods_issues == false) and (nodes_issues == false) %}
                    <span class="badge status-positive"><span class="icon">✅</span> All Services, Pods & Nodes OK</span>
                {% elif services_issues or pods_issues or nodes_issues %}
                    <span class="badge status-negative"><span class="icon">❌</span> Issues Detected — see details below</span>
                {% else %}
                    <span class="badge status-warning"><span class="icon">⚠️</span> Status Partially Unknown</span>
                {% endif %}
            </div>
        </div>
    </div>

    <!-- Quick badges -->
    <div class="card">
        <div class="badges">
            {% if services_issues %}
                <span class="badge status-negative"><span class="icon">❌</span> Services: Issues</span>
            {% elif services_issues == false %}
                <span class="badge status-positive"><span class="icon">✅</span> Services: OK</span>
            {% else %}
                <span class="badge status-warning"><span class="icon">⚠️</span> Services: Unknown</span>
            {% endif %}

            {% if pods_issues %}
                <span class="badge status-negative"><span class="icon">❌</span> Pods: Issues</span>
            {% elif pods_issues == false %}
                <span class="badge status-positive"><span class="icon">✅</span> Pods: OK</span>
            {% else %}
                <span class="badge status-warning"><span class="icon">⚠️</span> Pods: Unknown</span>
            {% endif %}

            {% if node_not_ready_fact is defined %}
                {% if nodes_issues %}
                    <span class="badge status-negative"><span class="icon">❌</span> Nodes: NotReady</span>
                {% else %}
                    <span class="badge status-positive"><span class="icon">✅</span> Nodes: OK</span>
                {% endif %}
            {% else %}
                <span class="badge status-unknown"><span class="icon">❔</span> Nodes: Not Collected</span>
            {% endif %}
        </div>
    </div>

    <!-- Issues Summary -->
    <div class="card section">
        <h2>Summary of Issues</h2>
        <div class="col-3">
            <div>
                <strong>Services</strong>
                <div class="scrollable">
                    {% if services_issues %}
                        <div class="problem-item"><strong>Failed Services:</strong><pre>{{ failed_out }}</pre></div>
                    {% else %}
                        <div class="muted">No failed services detected.</div>
                    {% endif %}
                </div>
            </div>
            <div>
                <strong>Pods</strong>
                <div class="scrollable">
                    {% if nonrun_out %}
                        <div class="problem-item"><strong>Non-running Pods:</strong><pre>{{ nonrun_out }}</pre></div>
                    {% endif %}
                    {% if zero_ready_out %}
                        <div class="problem-item"><strong>Pods with 0 Readiness:</strong><pre>{{ zero_ready_out }}</pre></div>
                    {% endif %}
                    {% if (not nonrun_out) and (not zero_ready_out) %}
                        <div class="muted">No problematic pods detected.</div>
                    {% endif %}
                </div>
            </div>
            <div>
                <strong>Nodes</strong>
                <div class="scrollable">
                    {% if node_not_ready_fact is defined %}
                        {% if node_not_ready_list | length > 0 %}
                            <div class="problem-item"><strong>NotReady Nodes:</strong>
                                <pre>{% for n in node_not_ready_list %}{{ n }}{% endfor %}</pre>
                            </div>
                        {% else %}
                            <div class="muted">All nodes Ready.</div>
                        {% endif %}
                    {% else %}
                        <div class="muted">Node readiness not collected.</div>
                    {% endif %}
                </div>
            </div>
        </div>
    </div>

    <!-- System Metrics -->
    <div class="card section">
        <h2>System Metrics</h2>
        <table>
            <tbody>
                <tr><td>OS Family</td><td>{{ ansible_distribution }} - {{ ansible_distribution_version }}</td></tr>
                <tr><td>BIOS Vendor</td><td>{{ vendor[0] | regex_replace('^.*Vendor:\\s*', '') | trim if vendor|length > 0 else 'N/A' }}</td></tr>
                <tr><td>Disk Usage (%)</td>
                    <td>
                        {% if disk_used is defined and disk_used.stdout %}
                            <span class="{% if disk_used.stdout | int >= disk_threshold %}status-negative{% else %}status-positive{% endif %}">{{ disk_used.stdout }}%</span>
                        {% else %}N/A{% endif %}
                    </td>
                </tr>
                <tr><td>Free Memory</td>
                    <td>
                        {% if mem_free_mb is defined %}
                            <span class="{% if mem_free_mb | int <= mem_threshold %}status-negative{% else %}status-positive{% endif %}">{{ mem_free_mb }} MB</span>
                        {% else %}N/A{% endif %}
                    </td>
                </tr>
                <tr><td>Load Average (1m)</td>
                    <td>
                        {% if ansible_loadavg is defined and ansible_loadavg['1m'] is defined %}
                            <span class="{% if ansible_loadavg['1m'] | float >= load_threshold %}status-negative{% else %}status-positive{% endif %}">{{ ansible_loadavg['1m'] }}</span>
                        {% else %}N/A{% endif %}
                    </td>
                </tr>
                <tr><td>Timezone</td><td>{% if timezone_info is defined and timezone_info.stdout %}<pre>{{ timezone_info.stdout }}</pre>{% else %}N/A{% endif %}</td></tr>
                <tr><td>Installed Packages</td>
                    <td>{% if ansible_facts.packages is defined %}
                        <div class="scrollable"><pre>{% for package in ansible_facts.packages.keys() | sort %}{{ package }}{% endfor %}</pre></div>
                        {% else %}<div class="muted">Package facts not available.</div>{% endif %}
                    </td>
                </tr>
                <tr><td>Running Services</td><td>{% if running_services is defined and running_services.stdout %}<div class="scrollable"><pre>{{ running_services.stdout }}</pre></div>{% else %}N/A{% endif %}</td></tr>
            </tbody>
        </table>
    </div>

    <!-- Kubernetes -->
    <div class="card section">
        <h2>Kubernetes Metrics</h2>
        <table>
            <tbody>
                <tr><td>All Pods</td><td>{% if all_pods is defined and all_pods.stdout %}<div class="scrollable"><pre>{{ all_pods.stdout }}</pre></div>{% else %}N/A{% endif %}</td></tr>
                <tr><td>Non-Running Pods</td><td>{% if nonrun_out %}<div class="scrollable"><pre>{{ nonrun_out }}</pre></div>{% else %}None{% endif %}</td></tr>
                <tr><td>Pods with 0 Readiness</td><td>{% if zero_ready_out %}<div class="scrollable"><pre>{{ zero_ready_out }}</pre></div>{% else %}None{% endif %}</td></tr>
                <tr><td>Warning Events</td><td>{% if warnings_out %}<div class="scrollable"><pre>{{ warnings_out }}</pre></div>{% else %}None{% endif %}</td></tr>
                <tr><td>Top Pods by CPU</td><td>{% if top_pods_cpu_out %}<div class="scrollable"><pre>{{ top_pods_cpu_out }}</pre></div>{% else %}N/A{% endif %}</td></tr>
                <tr><td>Top Nodes by CPU</td><td>{% if top_nodes_cpu_out %}<div class="scrollable"><pre>{{ top_nodes_cpu_out }}</pre></div>{% else %}N/A{% endif %}</td></tr>
            </tbody>
        </table>
    </div>
</body>
</html>
