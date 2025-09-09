<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8"/>
    <meta name="viewport" content="width=device-width, initial-scale=1"/>
    <title>System Healthcheck Report - {{ inventory_hostname }}</title>
    <style>
        body { font-family: Arial, sans-serif; margin: 20px; line-height: 1.5; color: #222; }
        .header { display: flex; gap: 20px; align-items: center; justify-content: space-between; }
        h1 { margin: 0 0 6px 0; font-size: 20px; color: #333; border-bottom: 2px solid #333; padding-bottom: 8px; }
        .meta { font-size: 13px; color: #444; }
        .badges { display:flex; gap:12px; align-items:center; margin-top:10px; }
        .badge { padding: 8px 12px; border-radius: 6px; font-weight:700; display:inline-flex; gap:8px; align-items:center; }
        .badge .icon { font-size:18px; line-height:1; }
        .status-positive { background:#e9f7ef; color:#1e7a34; border:1px solid #c6efd3; }
        .status-warning  { background:#fff8e6; color:#8a6b00; border:1px solid #ffe8a8; }
        .status-negative { background:#fdecea; color:#b02a37; border:1px solid #f6c6c6; }
        .status-unknown  { background:#eef3ff; color:#1f4ea6; border:1px solid #d6e3ff; }

        h2 { color:#444; margin-top:28px; background:#f6f6f6; padding:8px; border-left:4px solid #666; }
        table { width:100%; border-collapse:collapse; margin-top:12px; margin-bottom:28px; }
        th, td { border:1px solid #ddd; padding:8px; text-align:left; vertical-align:top; }
        th { background:#fafafa; position:sticky; top:0; font-weight:700; }
        tr:nth-child(even){ background:#fbfbfb; }
        .scrollable { max-height:220px; overflow:auto; border:1px solid #eee; padding:8px; background:#fff; }
        pre { margin:0; font-family:monospace; white-space:pre-wrap; word-wrap:break-word; }
        .problem-item { margin:4px 0; padding:6px 8px; border-radius:4px; background:#fff; border:1px dashed #eee; }
        .small { font-size:13px; color:#555; }
        .col-3 { display:flex; gap:12px; }
        .col-3 > div { flex:1; }
        .muted { color:#666; font-size:13px; }
    </style>
</head>
<body>
    <div class="header">
        <div>
            <h1>System Healthcheck Report - {{ ansible_hostname }}</h1>
            <div class="meta">
                <span><strong>Generated on:</strong> {{ ansible_date_time.iso8601 }}</span> &nbsp;•&nbsp;
                <span><strong>Hostname:</strong> {{ ansible_hostname }}</span> &nbsp;•&nbsp;
                <span><strong>IP:</strong> {{ ansible_all_ipv4_addresses | default('N/A') }}</span>
            </div>

            {# --- compute issue booleans and text safely --- #}
            {% set failed_out = failed_services.stdout | default('') | trim %}
            {% set nonrun_out = non_running_pods.stdout | default('') | trim %}
            {% set zero_ready_out = (zero_ready_pods_fact | join('\n')) if (zero_ready_pods_fact is defined and zero_ready_pods_fact) else '' %}
            {% set warnings_out = warnings.stdout | default('') | trim %}
            {% set top_pods_cpu_out = top_pods_cpu_fact | default('') %}
            {% set top_nodes_cpu_out = top_nodes_cpu.stdout | default('') %}
            {% set node_not_ready_list = node_not_ready_fact if (node_not_ready_fact is defined) else [] %}

            {% set services_issues = failed_out != '' %}
            {% set pods_issues = nonrun_out != '' or zero_ready_out != '' %}
            {% set warnings_issues = warnings_out != '' %}
            {% if node_not_ready_fact is defined %}
                {% set nodes_issues = (node_not_ready_list | length) > 0 %}
            {% else %}
                {% set nodes_issues = None %}
            {% endif %}

            {# determine overall status #}
            {% if (services_issues == false) and (pods_issues == false) and (nodes_issues == false) %}
                {% set overall_text = 'All Services, Pods & Nodes OK' %}
                {% set overall_class = 'status-positive' %}
                {% set overall_icon = '✅' %}
            {% elif services_issues or pods_issues or nodes_issues %}
                {% set overall_text = 'Issues Detected: See details below' %}
                {% set overall_class = 'status-negative' %}
                {% set overall_icon = '❌' %}
            {% else %}
                {% set overall_text = 'Status Partially Unknown — some node info missing' %}
                {% set overall_class = 'status-warning' %}
                {% set overall_icon = '⚠️' %}
            {% endif %}
        </div>

        <div style="text-align:right;">
            <div class="badges" aria-hidden="true">
                <span class="badge {{ overall_class }}" title="Overall">
                    <span class="icon">{{ overall_icon }}</span><span>{{ overall_text }}</span>
                </span>
            </div>
        </div>
    </div>

    <!-- per-area quick badges -->
    <div class="badges" style="margin-top:10px;">
        {# Services badge #}
        {% if services_issues %}
            <span class="badge status-negative"><span class="icon">❌</span> Services: Issues</span>
        {% elif services_issues == false %}
            <span class="badge status-positive"><span class="icon">✅</span> Services: OK</span>
        {% else %}
            <span class="badge status-warning"><span class="icon">⚠️</span> Services: Unknown</span>
        {% endif %}

        {# Pods badge #}
        {% if pods_issues %}
            <span class="badge status-negative"><span class="icon">❌</span> Pods: Issues</span>
        {% elif pods_issues == false %}
            <span class="badge status-positive"><span class="icon">✅</span> Pods: OK</span>
        {% else %}
            <span class="badge status-warning"><span class="icon">⚠️</span> Pods: Unknown</span>
        {% endif %}

        {# Nodes badge #}
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

    <!-- Short summary of what's wrong (if anything) -->
    <h2>Summary of Issues (servers, pods, nodes)</h2>
    <div class="col-3">
        <div>
            <strong>Services</strong>
            <div class="small muted">Failed services captured from <code>failed_services.stdout</code></div>
            <div class="scrollable">
                {% if services_issues %}
                    <div class="problem-item">
                        <strong>Failed Services Output:</strong>
                        <pre>{{ failed_out }}</pre>
                    </div>
                {% else %}
                    <div class="muted">No failed services detected.</div>
                {% endif %}
            </div>
        </div>

        <div>
            <strong>Pods</strong>
            <div class="small muted">Non-running / zero-readiness pods</div>
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
            <div class="small muted">Node readiness / NotReady list (if collected)</div>
            <div class="scrollable">
                {% if node_not_ready_fact is defined %}
                    {% if node_not_ready_list | length > 0 %}
                        <div class="problem-item"><strong>NotReady Nodes:</strong>
                            <pre>{% for n in node_not_ready_list %}{{ n }}
{% endfor %}</pre>
                        </div>
                    {% else %}
                        <div class="muted">All nodes appear Ready.</div>
                    {% endif %}
                {% else %}
                    <div class="muted">Node readiness facts not collected. (Consider setting a task to gather <code>node_not_ready_fact</code> or run <code>kubectl get nodes -o wide</code>.)</div>
                {% endif %}
            </div>
        </div>
    </div>

    <!-- Full System Metrics table -->
    <h2>System Metrics</h2>
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
                <td><strong>BIOS Version / Release</strong></td>
                <td>
                    {% set version = firmware_details | select('search', 'Version') | list %}
                    {% set release_date = firmware_details | select('search', 'Release Date') | list %}
                    <div class="small">
                        Version: {% if firmware_details is defined and version | length > 0 %}{{ version[0] | regex_replace('^.*Version:\\s*', '') | trim }}{% else %}N/A{% endif %} <br/>
                        Release: {% if firmware_details is defined and release_date | length > 0 %}{{ release_date[0] | regex_replace('^.*Release Date:\\s*', '') | trim }}{% else %}N/A{% endif %}
                    </div>
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
                <td><strong>Load Average (1m)</strong></td>
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
                    {% if timezone_info is defined and timezone_info.stdout %}<pre>{{ timezone_info.stdout }}</pre>{% else %}N/A{% endif %}
                </td>
            </tr>
            <tr>
                <td><strong>Installed Packages</strong></td>
                <td>
                    {% if ansible_facts.packages is defined %}
                        <div class="scrollable">
                            <pre>{% for package in ansible_facts.packages.keys() | sort %}{{ package }}
{% endfor %}</pre>
                        </div>
                    {% else %}
                        <div class="muted">Package facts not available.</div>
                    {% endif %}
                </td>
            </tr>
            <tr>
                <td><strong>Running Services</strong></td>
                <td>
                    {% if running_services is defined and running_services.stdout %}
                        <div class="scrollable"><pre>{{ running_services.stdout }}</pre></div>
                    {% else %}
                        N/A
                    {% endif %}
                </td>
            </tr>
            <tr>
                <td><strong>Failed Services (details)</strong></td>
                <td>
                    {% if services_issues %}
                        <div class="scrollable"><pre>{{ failed_out }}</pre></div>
                    {% else %}
                        None
                    {% endif %}
                </td>
            </tr>
            <tr>
                <td><strong>System Error Logs</strong></td>
                <td>
                    {% if error_logs is defined and error_logs.stdout %}
                        <div class="scrollable"><pre>{{ error_logs.stdout }}</pre></div>
                    {% else %}
                        None
                    {% endif %}
                </td>
            </tr>
            <tr>
                <td><strong>Logrotate configured on</strong></td>
                <td>
                    {% if logrotate_services_fact is defined and logrotate_services_fact %}
                        <div class="scrollable"><pre>{% for service in logrotate_services_fact %}{{ service }}
{% endfor %}</pre></div>
                    {% else %}
                        N/A
                    {% endif %}
                </td>
            </tr>
        </tbody>
    </table>

    <!-- Kubernetes section -->
    <h2>Kubernetes Metrics & Problems</h2>
    <table>
        <tbody>
            <tr>
                <td><strong>All Pods</strong></td>
                <td>
                    {% if all_pods is defined and all_pods.stdout %}
                        <div class="scrollable"><pre>{{ all_pods.stdout }}</pre></div>
                    {% else %}
                        N/A
                    {% endif %}
                </td>
            </tr>
            <tr>
                <td><strong>Non-Running Pods</strong></td>
                <td>
                    {% if nonrun_out %}
                        <div class="scrollable"><pre>{{ nonrun_out }}</pre></div>
                    {% else %}
                        None
                    {% endif %}
                </td>
            </tr>
            <tr>
                <td><strong>Pods with 0 Readiness</strong></td>
                <td>
                    {% if zero_ready_out %}
                        <div class="scrollable"><pre>{{ zero_ready_out }}</pre></div>
                    {% else %}
                        None
                    {% endif %}
                </td>
            </tr>
            <tr>
                <td><strong>Warning Events</strong></td>
                <td>
                    {% if warnings_out %}
                        <div class="scrollable"><pre>{{ warnings_out }}</pre></div>
                    {% else %}
                        None
                    {% endif %}
                </td>
            </tr>
            <tr>
                <td><strong>Top Pods by CPU</strong></td>
                <td>
                    {% if top_pods_cpu_out %}
                        <div class="scrollable"><pre>{{ top_pods_cpu_out }}</pre></div>
                    {% else %}
                        N/A
                    {% endif %}
                </td>
            </tr>
            <tr>
                <td><strong>Top Nodes by CPU</strong></td>
                <td>
                    {% if top_nodes_cpu_out %}
                        <div class="scrollable"><pre>{{ top_nodes_cpu_out }}</pre></div>
                    {% else %}
                        N/A
                    {% endif %}
                </td>
            </tr>
            <tr>
                <td><strong>Nodes NotReady (if collected)</strong></td>
                <td>
                    {% if node_not_ready_fact is defined %}
                        {% if node_not_ready_list | length > 0 %}
                            <div class="scrollable"><pre>{% for n in node_not_ready_list %}{{ n }}
{% endfor %}</pre></div>
                        {% else %}
                            All nodes Ready.
                        {% endif %}
                    {% else %}
                        <div class="muted">Not collected. Run a task to gather node readiness or set <code>node_not_ready_fact</code>.</div>
                    {% endif %}
                </td>
            </tr>
        </tbody>
    </table>

    <div class="muted small">Tip: To get node readiness automatically, add a task that sets <code>node_not_ready_fact</code> (e.g. parse <code>kubectl get nodes -o jsonpath='{range .items[?(@.status.conditions[?(@.type==\"Ready\")].status==\"False\")]}{.metadata.name}{\"\\n\"}{end}'</code> or run an Ansible k8s_info lookup).</div>
</body>
</html>
