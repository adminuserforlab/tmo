<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>System Healthcheck Report - {{ inventory_hostname }}</title>
  <style>
    body { font-family: Arial, sans-serif; margin: 20px; }
    h1 { text-align: center; }
    .ok { color: green; font-weight: bold; }
    .not-ok { color: red; font-weight: bold; }
    pre { background: #f4f4f4; padding: 10px; border-radius: 8px; }
    .section { margin-bottom: 25px; }
  </style>
</head>
<body>
  <h1>System Healthcheck Report</h1>
  <h2>Host: {{ inventory_hostname }}</h2>
  <p><b>Generated At:</b> {{ report_timestamp }}</p>

  <div class="section">
    <h3>Disk Usage</h3>
    <p>Used: {{ disk_used.stdout | default('N/A') }}%</p>
    {% if (disk_used.stdout | int) > disk_threshold %}
      <p class="not-ok">Disk usage above threshold ({{ disk_threshold }}%)</p>
    {% else %}
      <p class="ok">Disk usage is healthy</p>
    {% endif %}
  </div>

  <div class="section">
    <h3>Memory</h3>
    <p>Free Memory: {{ mem_free_mb | default('N/A') }} MB</p>
    {% if (mem_free_mb | int) < mem_threshold %}
      <p class="not-ok">Memory below threshold ({{ mem_threshold }} MB)</p>
    {% else %}
      <p class="ok">Memory is healthy</p>
    {% endif %}
  </div>

  <div class="section">
    <h3>Services</h3>
    {% if services_issues | default(false) %}
      <p class="not-ok">Some services have FAILED:</p>
      <pre>{{ failed_services.stdout | default('No data') }}</pre>
    {% else %}
      <p class="ok">All critical services are running</p>
    {% endif %}
  </div>

  <div class="section">
    <h3>Pods</h3>
    {% if pods_issues | default(false) %}
      <p class="not-ok">Some pods are NOT healthy:</p>
      <pre>{{ non_running_pods.stdout | default('') }}</pre>
      <pre>{{ zero_ready_pods_fact | default('') }}</pre>
    {% else %}
      <p class="ok">All pods are running and ready</p>
    {% endif %}
  </div>

  <div class="section">
    <h3>Nodes</h3>
    {% if nodes_issues | default(false) %}
      <p class="not-ok">Some nodes are NOT Ready:</p>
      <pre>{{ node_not_ready_fact | default([]) | join('\n') }}</pre>
    {% else %}
      <p class="ok">All nodes are Ready</p>
    {% endif %}
  </div>

  <div class="section">
    <h3>Warnings</h3>
    <pre>{{ warnings.stdout | default('No warnings') }}</pre>
  </div>

  <div class="section">
    <h3>Top Pods by CPU</h3>
    <pre>{{ top_pods_cpu_fact | default('No data') }}</pre>
  </div>

  <div class="section">
    <h3>Top Nodes by CPU</h3>
    <pre>{{ top_nodes_cpu.stdout | default('No data') }}</pre>
  </div>

  <div class="section">
    <h3>Error Logs (journalctl)</h3>
    <pre>{{ error_logs.stdout | default('No recent errors') }}</pre>
  </div>

</body>
</html>
