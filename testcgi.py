<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>NPC Health & Diagnostic Report - {{ ansible_date_time.date }} {{ ansible_date_time.time }}</title>
  <style>
    body { font-family: Arial, sans-serif; margin: 20px; background: #f8f9fa; color: #333; }
    h1 { text-align: center; color: #004085; }
    .summary { padding: 15px; background: #fff3cd; border: 1px solid #ffeeba; margin-bottom: 20px; }
    .summary h2 { margin-top: 0; }
    .section { margin-bottom: 20px; background: #fff; border: 1px solid #ddd; border-radius: 8px; }
    .section h3 { background: #007bff; color: #fff; padding: 10px; margin: 0; border-radius: 8px 8px 0 0; }
    .content { max-height: 200px; overflow-y: auto; padding: 10px; font-family: monospace; background: #f1f1f1; }
    .ok { color: green; font-weight: bold; }
    .notok { color: red; font-weight: bold; }
  </style>
</head>
<body>
  <h1>NPC Health & Diagnostic Report</h1>

  <!-- Summary Section -->
  <div class="summary">
    <h2>⚠️ Summary of Issues</h2>
    <ul>
      {% if failed_services.stdout_lines %}
        <li class="notok">Failed Services: {{ failed_services.stdout_lines|length }}</li>
      {% else %}
        <li class="ok">All Services OK</li>
      {% endif %}

      {% if non_running_pods.stdout_lines %}
        <li class="notok">Non-running Pods: {{ non_running_pods.stdout_lines|length }}</li>
      {% else %}
        <li class="ok">All Pods Running</li>
      {% endif %}

      {% if not_ready_pods.stdout_lines %}
        <li class="notok">Pods Not Ready: {{ not_ready_pods.stdout_lines|length }}</li>
      {% else %}
        <li class="ok">All Pods Ready</li>
      {% endif %}

      {% if warnings.stdout_lines %}
        <li class="notok">K8s Warnings Found</li>
      {% else %}
        <li class="ok">No Critical Warnings</li>
      {% endif %}
    </ul>
  </div>

  <!-- Scrollable Sections -->
  <div class="section">
    <h3>🚨 Failed Services</h3>
    <div class="content">{{ failed_services.stdout | default('None') | replace('\n', '<br>') }}</div>
  </div>

  <div class="section">
    <h3>🐳 Non-running Pods</h3>
    <div class="content">{{ non_running_pods.stdout | default('None') | replace('\n', '<br>') }}</div>
  </div>

  <div class="section">
    <h3>⚠️ Not Ready Pods</h3>
    <div class="content">{{ not_ready_pods.stdout | default('None') | replace('\n', '<br>') }}</div>
  </div>

  <div class="section">
    <h3>📜 K8s Warnings</h3>
    <div class="content">{{ warnings.stdout | default('None') | replace('\n', '<br>') }}</div>
  </div>

  <div class="section">
    <h3>📊 Top Pods CPU</h3>
    <div class="content">{{ top_pods_cpu_fact | default('N/A') | replace('\n', '<br>') }}</div>
  </div>

  <div class="section">
    <h3>🖥️ Top Nodes CPU</h3>
    <div class="content">{{ top_nodes_cpu.stdout | default('N/A') | replace('\n', '<br>') }}</div>
  </div>

</body>
</html>
